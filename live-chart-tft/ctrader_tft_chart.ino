/*
 * ctrader_tft_chart.ino
 *
 * ESP32 + TFT candlestick chart with live cTrader data.
 * Connects to the relay_server.py via WebSocket, receives
 * candle data and live ticks, renders candlestick chart
 * with position overlays.
 *
 * Hardware:
 *   - ESP32 (WROOM-32 or similar)
 *   - ILI9341 320x240 TFT (SPI) — easily adapted for ST7789 or ILI9488
 *   - 2 buttons: BTN_SYMBOL (cycle symbol), BTN_PERIOD (cycle timeframe)
 *
 * Libraries needed (Arduino Library Manager):
 *   - TFT_eSPI (configure User_Setup.h for your display)
 *   - ArduinoWebsockets
 *   - ArduinoJson
 *
 * Wiring (example for ILI9341):
 *   TFT_CS   → GPIO 15
 *   TFT_DC   → GPIO 2
 *   TFT_RST  → GPIO 4
 *   TFT_MOSI → GPIO 23 (VSPI)
 *   TFT_SCLK → GPIO 18 (VSPI)
 *   TFT_LED  → 3.3V
 *   BTN_SYM  → GPIO 32 (pull-up, active LOW)
 *   BTN_TF   → GPIO 33 (pull-up, active LOW)
 */

#include <WiFi.h>
#include <ArduinoWebsockets.h>
#include <ArduinoJson.h>
#include <TFT_eSPI.h>

using namespace websockets;

// ─── Configuration ──────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASS     = "YOUR_WIFI_PASS";
const char* WS_HOST       = "192.168.1.100";  // your PC's IP
const int   WS_PORT       = 8765;

const int BTN_SYMBOL = 32;
const int BTN_PERIOD = 33;

// ─── Display ────────────────────────────────────────────────
TFT_eSPI tft = TFT_eSPI();

// Screen layout constants
const int SCREEN_W = 320;
const int SCREEN_H = 240;
const int HEADER_H = 24;         // top bar: symbol, price, spread
const int CHART_Y  = HEADER_H;
const int CHART_H  = SCREEN_H - HEADER_H - 20;  // leave 20px for bottom bar
const int CHART_W  = SCREEN_W - 40;  // 40px for price axis
const int CHART_X  = 0;
const int AXIS_X   = CHART_X + CHART_W;  // price axis starts here
const int BOTTOM_Y = SCREEN_H - 20;

// Colors
const uint16_t COL_BG       = 0x0841;  // very dark blue-gray
const uint16_t COL_HEADER   = 0x1082;
const uint16_t COL_GRID     = 0x18E3;
const uint16_t COL_TEXT     = 0xC618;
const uint16_t COL_DIM      = 0x630C;
const uint16_t COL_GREEN    = 0x2DC4;
const uint16_t COL_RED      = 0xD8A4;
const uint16_t COL_GREEN_W  = 0x07E0;  // bright green wick
const uint16_t COL_RED_W    = 0xF800;  // bright red wick
const uint16_t COL_BID      = 0x07FF;  // cyan for bid line
const uint16_t COL_POS_BUY  = 0x07E0;  // green position line
const uint16_t COL_POS_SELL = 0xF800;  // red position line

// ─── Data Structures ───────────────────────────────────────
struct Candle {
  long   ts;      // timestamp in ms
  float  open;
  float  high;
  float  low;
  float  close;
};

struct Position {
  char   symbol[16];
  char   side[5];    // BUY or SELL
  float  price;
  float  lots;
};

#define MAX_CANDLES   80
#define MAX_POSITIONS 10

Candle   candleData[MAX_CANDLES];
int      candleCount = 0;

Position posData[MAX_POSITIONS];
int      posCount = 0;

float    currentBid = 0;
float    currentAsk = 0;

// Available symbols and periods (received from relay)
char     symbols[6][16];
int      symbolCount = 0;
int      currentSymbolIdx = 0;

const char* periods[] = {"M1", "M5", "M15", "H1"};
const int   periodCount = 4;
int         currentPeriodIdx = 1;  // default M5

bool     needsRedraw = true;
bool     connected = false;

// ─── WebSocket ──────────────────────────────────────────────
WebsocketsClient wsClient;

unsigned long lastReconnect = 0;
const unsigned long RECONNECT_INTERVAL = 5000;

unsigned long lastBtnSymbol = 0;
unsigned long lastBtnPeriod = 0;
const unsigned long DEBOUNCE = 300;

// ─── Forward Declarations ───────────────────────────────────
void drawChart();
void drawHeader();
void drawBottomBar();
void sendSubscribe();
void handleMessage(const char* data, size_t len);

// ─── Setup ──────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println("\n=== cTrader TFT Chart ===\n");

  // Buttons
  pinMode(BTN_SYMBOL, INPUT_PULLUP);
  pinMode(BTN_PERIOD, INPUT_PULLUP);

  // Display init
  tft.init();
  tft.setRotation(1);  // landscape
  tft.fillScreen(COL_BG);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setTextSize(1);

  // Splash
  tft.setTextDatum(MC_DATUM);
  tft.drawString("Connecting to WiFi...", SCREEN_W / 2, SCREEN_H / 2);

  // WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\nWiFi connected: %s\n", WiFi.localIP().toString().c_str());
    tft.fillScreen(COL_BG);
    tft.drawString("WiFi OK. Connecting to relay...", SCREEN_W / 2, SCREEN_H / 2);
  } else {
    tft.fillScreen(COL_BG);
    tft.drawString("WiFi FAILED", SCREEN_W / 2, SCREEN_H / 2);
    return;
  }

  // WebSocket callbacks
  wsClient.onMessage([](WebsocketsMessage msg) {
    handleMessage(msg.c_str(), msg.length());
  });

  wsClient.onEvent([](WebsocketsEvent event, String data) {
    if (event == WebsocketsEvent::ConnectionOpened) {
      Serial.println("WS connected");
      connected = true;
      sendSubscribe();
    } else if (event == WebsocketsEvent::ConnectionClosed) {
      Serial.println("WS disconnected");
      connected = false;
    }
  });

  // Initial connect
  connectWS();
}

// ─── WebSocket Connection ───────────────────────────────────
void connectWS() {
  char url[128];
  snprintf(url, sizeof(url), "ws://%s:%d", WS_HOST, WS_PORT);
  Serial.printf("Connecting to %s\n", url);
  wsClient.connect(url);
}

void sendSubscribe() {
  if (!connected || symbolCount == 0) return;

  StaticJsonDocument<128> doc;
  doc["cmd"] = "subscribe";
  doc["symbol"] = symbols[currentSymbolIdx];
  doc["period"] = periods[currentPeriodIdx];

  char buf[128];
  serializeJson(doc, buf);
  wsClient.send(buf);

  Serial.printf("Subscribed: %s %s\n", symbols[currentSymbolIdx], periods[currentPeriodIdx]);
  needsRedraw = true;
}

// ─── Message Handler ────────────────────────────────────────
void handleMessage(const char* data, size_t len) {
  // Use a large doc for candle arrays
  DynamicJsonDocument doc(16384);
  DeserializationError err = deserializeJson(doc, data, len);
  if (err) {
    Serial.printf("JSON error: %s\n", err.c_str());
    return;
  }

  const char* type = doc["type"] | "";

  if (strcmp(type, "config") == 0) {
    // Receive available symbols
    JsonArray syms = doc["symbols"].as<JsonArray>();
    symbolCount = 0;
    for (JsonVariant v : syms) {
      if (symbolCount < 6) {
        strncpy(symbols[symbolCount], v.as<const char*>(), 15);
        symbols[symbolCount][15] = '\0';
        symbolCount++;
      }
    }
    Serial.printf("Config: %d symbols\n", symbolCount);

    if (symbolCount > 0 && !connected) {
      // Will subscribe on connect
    } else if (symbolCount > 0) {
      sendSubscribe();
    }

  } else if (strcmp(type, "candles") == 0) {
    // Historical candle batch
    JsonArray arr = doc["data"].as<JsonArray>();
    candleCount = 0;
    for (JsonVariant v : arr) {
      if (candleCount >= MAX_CANDLES) break;
      JsonArray c = v.as<JsonArray>();
      candleData[candleCount].ts    = c[0].as<long>();
      candleData[candleCount].open  = c[1].as<float>();
      candleData[candleCount].high  = c[2].as<float>();
      candleData[candleCount].low   = c[3].as<float>();
      candleData[candleCount].close = c[4].as<float>();
      candleCount++;
    }
    Serial.printf("Candles received: %d\n", candleCount);
    needsRedraw = true;

  } else if (strcmp(type, "candle_update") == 0) {
    // Live candle update (update last or append)
    JsonArray c = doc["candle"].as<JsonArray>();
    long   ts    = c[0].as<long>();
    float  o     = c[1].as<float>();
    float  h     = c[2].as<float>();
    float  l     = c[3].as<float>();
    float  cl    = c[4].as<float>();

    if (candleCount > 0 && candleData[candleCount - 1].ts == ts) {
      // Update current candle
      candleData[candleCount - 1].open  = o;
      candleData[candleCount - 1].high  = h;
      candleData[candleCount - 1].low   = l;
      candleData[candleCount - 1].close = cl;
    } else {
      // New candle — shift if full
      if (candleCount >= MAX_CANDLES) {
        memmove(&candleData[0], &candleData[1], sizeof(Candle) * (MAX_CANDLES - 1));
        candleCount = MAX_CANDLES - 1;
      }
      candleData[candleCount].ts    = ts;
      candleData[candleCount].open  = o;
      candleData[candleCount].high  = h;
      candleData[candleCount].low   = l;
      candleData[candleCount].close = cl;
      candleCount++;
    }
    needsRedraw = true;

  } else if (strcmp(type, "tick") == 0) {
    float newBid = doc["bid"] | 0.0f;
    float newAsk = doc["ask"] | 0.0f;

    if (newBid != currentBid || newAsk != currentAsk) {
      currentBid = newBid;
      currentAsk = newAsk;
      // Only redraw header for ticks, not full chart
      drawHeader();
    }

  } else if (strcmp(type, "positions") == 0) {
    JsonArray arr = doc["data"].as<JsonArray>();
    posCount = 0;
    for (JsonVariant v : arr) {
      if (posCount >= MAX_POSITIONS) break;
      strncpy(posData[posCount].symbol, v["symbol"] | "", 15);
      strncpy(posData[posCount].side,   v["side"]   | "", 4);
      posData[posCount].price = v["price"] | 0.0f;
      posData[posCount].lots  = v["lots"]  | 0.0f;
      posCount++;
    }
    needsRedraw = true;
  }
}

// ─── Drawing ────────────────────────────────────────────────
void drawHeader() {
  tft.fillRect(0, 0, SCREEN_W, HEADER_H, COL_HEADER);

  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(COL_TEXT, COL_HEADER);
  tft.setTextSize(1);

  // Symbol + period
  char label[32];
  if (symbolCount > 0) {
    snprintf(label, sizeof(label), "%s  %s", symbols[currentSymbolIdx], periods[currentPeriodIdx]);
  } else {
    snprintf(label, sizeof(label), "---  ---");
  }
  tft.drawString(label, 4, 4);

  // Bid/Ask
  if (currentBid > 0) {
    char priceStr[48];
    float spread = (currentAsk - currentBid) * 100000;  // in pips (5-digit)
    snprintf(priceStr, sizeof(priceStr), "B:%.5f  A:%.5f  S:%.1f",
             currentBid, currentAsk, spread);

    // Color bid based on last candle direction
    uint16_t priceColor = COL_TEXT;
    if (candleCount > 0) {
      priceColor = candleData[candleCount - 1].close >= candleData[candleCount - 1].open
                   ? COL_GREEN : COL_RED;
    }
    tft.setTextColor(priceColor, COL_HEADER);
    tft.setTextDatum(TR_DATUM);
    tft.drawString(priceStr, SCREEN_W - 4, 4);
  }

  // Connection indicator
  tft.fillCircle(SCREEN_W - 8, HEADER_H - 6, 3, connected ? COL_GREEN : COL_RED);
}

void drawBottomBar() {
  tft.fillRect(0, BOTTOM_Y, SCREEN_W, 20, COL_HEADER);
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(COL_DIM, COL_HEADER);
  tft.setTextSize(1);

  // Position summary for current symbol
  int openPos = 0;
  float totalLots = 0;
  for (int i = 0; i < posCount; i++) {
    if (symbolCount > 0 && strcmp(posData[i].symbol, symbols[currentSymbolIdx]) == 0) {
      openPos++;
      totalLots += posData[i].lots;
    }
  }

  char info[64];
  if (openPos > 0) {
    snprintf(info, sizeof(info), "Positions: %d (%.2f lots)  |  Candles: %d",
             openPos, totalLots, candleCount);
  } else {
    snprintf(info, sizeof(info), "No positions  |  Candles: %d", candleCount);
  }
  tft.drawString(info, 4, BOTTOM_Y + 4);

  // Symbol cycle hint
  tft.setTextDatum(TR_DATUM);
  char hint[32];
  snprintf(hint, sizeof(hint), "[SYM] [TF]");
  tft.drawString(hint, SCREEN_W - 4, BOTTOM_Y + 4);
}

void drawChart() {
  if (candleCount == 0) {
    tft.fillRect(CHART_X, CHART_Y, SCREEN_W, CHART_H, COL_BG);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(COL_DIM, COL_BG);
    tft.drawString("Waiting for candles...", SCREEN_W / 2, CHART_Y + CHART_H / 2);
    return;
  }

  // Clear chart area
  tft.fillRect(CHART_X, CHART_Y, SCREEN_W, CHART_H, COL_BG);

  // Determine visible candles (fit to screen width)
  int candleWidth = 4;     // body width in pixels
  int candleGap   = 1;     // gap between candles
  int totalCandleW = candleWidth + candleGap;
  int maxVisible = CHART_W / totalCandleW;
  int visibleCount = min(candleCount, maxVisible);
  int startIdx = candleCount - visibleCount;

  // Find price range
  float priceHigh = -1e9;
  float priceLow  = 1e9;

  for (int i = startIdx; i < candleCount; i++) {
    if (candleData[i].high > priceHigh) priceHigh = candleData[i].high;
    if (candleData[i].low  < priceLow)  priceLow  = candleData[i].low;
  }

  // Include position prices in range
  for (int i = 0; i < posCount; i++) {
    if (symbolCount > 0 && strcmp(posData[i].symbol, symbols[currentSymbolIdx]) == 0) {
      if (posData[i].price > priceHigh) priceHigh = posData[i].price;
      if (posData[i].price < priceLow)  priceLow  = posData[i].price;
    }
  }

  // Add padding
  float range = priceHigh - priceLow;
  if (range < 0.00001) range = 0.00010;
  float padding = range * 0.08;
  priceHigh += padding;
  priceLow  -= padding;
  range = priceHigh - priceLow;

  // Price-to-Y mapping
  auto priceToY = [&](float price) -> int {
    return CHART_Y + (int)((priceHigh - price) / range * CHART_H);
  };

  // Draw grid lines (4 horizontal)
  tft.setTextColor(COL_DIM, COL_BG);
  tft.setTextSize(1);
  for (int i = 0; i <= 4; i++) {
    float gridPrice = priceLow + (range * i / 4.0);
    int y = priceToY(gridPrice);
    // Dotted line
    for (int x = CHART_X; x < AXIS_X; x += 4) {
      tft.drawPixel(x, y, COL_GRID);
    }
    // Price label on axis
    char pLabel[12];
    snprintf(pLabel, sizeof(pLabel), "%.5f", gridPrice);
    tft.setTextDatum(ML_DATUM);
    tft.drawString(pLabel, AXIS_X + 2, y);
  }

  // Draw candles
  for (int i = 0; i < visibleCount; i++) {
    int idx = startIdx + i;
    Candle &c = candleData[idx];

    int x = CHART_X + i * totalCandleW;
    int yOpen  = priceToY(c.open);
    int yClose = priceToY(c.close);
    int yHigh  = priceToY(c.high);
    int yLow   = priceToY(c.low);

    bool bullish = c.close >= c.open;
    uint16_t bodyColor = bullish ? COL_GREEN : COL_RED;
    uint16_t wickColor = bullish ? COL_GREEN_W : COL_RED_W;

    int bodyTop = min(yOpen, yClose);
    int bodyBot = max(yOpen, yClose);
    int bodyH   = bodyBot - bodyTop;
    if (bodyH < 1) bodyH = 1;

    // Wick (center of candle body)
    int wickX = x + candleWidth / 2;
    tft.drawLine(wickX, yHigh, wickX, yLow, wickColor);

    // Body
    if (bullish) {
      tft.fillRect(x, bodyTop, candleWidth, bodyH, bodyColor);
    } else {
      tft.fillRect(x, bodyTop, candleWidth, bodyH, bodyColor);
    }
  }

  // Draw current bid line (dashed)
  if (currentBid > priceLow && currentBid < priceHigh) {
    int bidY = priceToY(currentBid);
    for (int x = CHART_X; x < AXIS_X; x += 6) {
      tft.drawLine(x, bidY, min(x + 3, (int)AXIS_X), bidY, COL_BID);
    }
    // Small label
    tft.setTextDatum(MR_DATUM);
    tft.setTextColor(COL_BID, COL_BG);
    char bidLabel[10];
    snprintf(bidLabel, sizeof(bidLabel), "%.5f", currentBid);
    tft.fillRect(AXIS_X, bidY - 5, 40, 10, COL_BID);
    tft.setTextColor(TFT_BLACK, COL_BID);
    tft.drawString(bidLabel, SCREEN_W - 2, bidY);
  }

  // Draw position lines
  for (int i = 0; i < posCount; i++) {
    if (symbolCount == 0 || strcmp(posData[i].symbol, symbols[currentSymbolIdx]) != 0)
      continue;

    float pp = posData[i].price;
    if (pp < priceLow || pp > priceHigh) continue;

    int posY = priceToY(pp);
    uint16_t posColor = (strcmp(posData[i].side, "BUY") == 0) ? COL_POS_BUY : COL_POS_SELL;

    // Dashed line
    for (int x = CHART_X; x < AXIS_X; x += 8) {
      tft.drawLine(x, posY, min(x + 4, (int)AXIS_X), posY, posColor);
    }

    // Label
    char posLabel[24];
    snprintf(posLabel, sizeof(posLabel), "%s %.2fL", posData[i].side, posData[i].lots);
    tft.setTextDatum(TL_DATUM);
    tft.setTextColor(posColor, COL_BG);
    tft.drawString(posLabel, CHART_X + 2, posY - 10);
  }
}

// ─── Button Handling ────────────────────────────────────────
void checkButtons() {
  unsigned long now = millis();

  // Symbol cycle button
  if (digitalRead(BTN_SYMBOL) == LOW && now - lastBtnSymbol > DEBOUNCE) {
    lastBtnSymbol = now;
    if (symbolCount > 0) {
      currentSymbolIdx = (currentSymbolIdx + 1) % symbolCount;
      Serial.printf("Symbol → %s\n", symbols[currentSymbolIdx]);
      candleCount = 0;  // clear chart
      sendSubscribe();
    }
  }

  // Period cycle button
  if (digitalRead(BTN_PERIOD) == LOW && now - lastBtnPeriod > DEBOUNCE) {
    lastBtnPeriod = now;
    currentPeriodIdx = (currentPeriodIdx + 1) % periodCount;
    Serial.printf("Period → %s\n", periods[currentPeriodIdx]);
    candleCount = 0;
    sendSubscribe();
  }
}

// ─── Main Loop ──────────────────────────────────────────────
void loop() {
  // WebSocket poll
  if (connected) {
    wsClient.poll();
  } else {
    // Reconnect
    unsigned long now = millis();
    if (now - lastReconnect > RECONNECT_INTERVAL) {
      lastReconnect = now;
      connectWS();
    }
  }

  // Buttons
  checkButtons();

  // Redraw if needed
  if (needsRedraw) {
    needsRedraw = false;
    drawHeader();
    drawChart();
    drawBottomBar();
  }

  delay(10);  // small yield
}
