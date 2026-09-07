//+------------------------------------------------------------------+
//| GoldBolt_Client.mq4                                              |
//| 纯执行器 - 所有策略逻辑在服务端                                     |
//| EA 只负责：风控参数 + 执行指令 + 推送数据                           |
//| v2.5: 服务器重启自动恢复连接                                        |
//+------------------------------------------------------------------+
#property copyright "Gold Bolt"
#property version   "2.9.5"
#property strict

// 引入标准库
#include <StdLib.mqh>

// ============ 版本信息 ============
#define EA_VERSION  "2.9.5"
#define EA_BUILD    15

//+------------------------------------------------------------------+
//| 服务器连接配置                                                      |
//+------------------------------------------------------------------+
extern string   ServerURL       = "http://127.0.0.1:8880";  // 服务端地址
extern string   AccountID       = "account_A";              // 账户 ID
extern string   ApiToken        = "";                       // API Token

//+------------------------------------------------------------------+
//| 风控参数配置（用户自行调整）                                        |
//+------------------------------------------------------------------+
extern double   MaxRiskPercent  = 2.0;      // 单笔最大风险 %
extern int      MaxPositions    = 5;        // 最大持仓数
extern double   MaxDailyLoss    = 5.0;      // 日最大亏损 %
extern double   MaxSpread       = 5.0;      // 最大点差（points）
extern int      MaxSameDir      = 3;        // 同方向最大持仓数
extern double   MaxFloatLoss    = 3.0;      // 最大浮亏 %
extern bool     UseFixedLots    = true;     // 优先固定手数
extern double   FixedLots       = 0.10;     // 固定手数（UseFixedLots=true 时生效；SymbolLotsMap 未命中时回退）
extern string   SymbolLotsMap   = "";       // 按品种手数：XAUUSD:0.10,US100:0.05（空=全部用 FixedLots）
extern string   StrategyLotsMap = "";       // 按策略手数：ai_signal:0.01,pullback:0.10（空=不覆盖）

//+------------------------------------------------------------------+
//| 策略启用配置（EA 端控制）                                           |
//+------------------------------------------------------------------+
input group "===== 策略开关与Magic编号 ====="
extern bool     EnablePullback      = true;     // 📈 趋势回调策略
extern int      PullbackMagic       = 20250231; //趋势回调 Magic

extern bool     EnableBreakout      = true;     // 🔥 突破回踩策略
extern int      BreakoutMagic       = 20250232; // 突破回踩 Magic

extern bool     EnableDivergence    = true;     // 📊 RSI 背离策略
extern int      DivergenceMagic     = 20250233; // RSI 背离 Magic

extern bool     EnablePyramid       = true;     // 🏗️ 突破加仓策略
extern int      PyramidMagic        = 20250234; // 突破加仓 Magic

extern bool     EnableCounter       = false;    // 🔄 反向回调加仓
extern int      CounterMagic        = 20250235; // 反向回调 Magic

extern bool     EnableRange        = false;    // 📊 震荡市区间策略
extern int      RangeMagic        = 20250236; // 震荡市区间 Magic

extern bool     EnableMomentumScalp       = false;    // ⚡ 动量剥头皮策略
extern int      MomentumScalpMagic        = 20250237; // 动量剥头皮 Magic
extern bool     MomentumScalpUseFixedLots = true;     // 动量剥头皮使用固定手数
extern double   MomentumScalpFixedLots    = 0.05;     // 动量剥头皮固定手数
extern double   MomentumScalpRiskPercent  = 0.5;      // 动量剥头皮单笔风险 %

extern bool     EnableAISignal      = true;     // 🤖 AI 信号挂单策略
extern int      AISignalMagic       = 20250238; // AI 信号 Magic

extern bool     EnableScaleIn       = true;     // ➕ 浮亏加仓策略
extern int      ScaleInMagic        = 20250239; // 浮亏加仓 Magic

//+------------------------------------------------------------------+
//| 通信参数配置                                                       |
//+------------------------------------------------------------------+
extern int      PollInterval    = 5;        // 轮询间隔（秒）
extern int      BarInterval     = 60;       // K 线发送间隔（秒）
extern int      BarCount        = 50;  // K 线数量      // K 线数量
extern string   Symbols         = "XAUUSD"; // 交易品种（逗号分隔多个）
extern string   AISymbols       = "";      // AI 分析品种（逗号分隔，留空=与交易品种相同）
extern string   SymbolSuffix    = "";       // 经纪商品种后缀（如 .m, m#, _m），留空=无后缀
extern int      Slippage        = 3;        // 滑点（点数）

input group "===== 可视化桥接 ====="
extern bool     EnableVisualBridge       = true;
extern int      VisualBridgePollSeconds  = 5;
extern int      VisualBridgeTimeoutMs    = 1500;
extern string   VisualBridgeTimeframes   = "M1,M5,M15,M30,H1,H4,D1";
extern bool     VisualBridgeCommonFiles  = true;

input group "===== 谐波指标可视化 ====="
extern bool     EnableHarmonicVisuals   = false;   // 可选：显示本地 Shepherd 谐波对象，需安装指标文件并手动启用
extern bool     EnableHarmonicReport    = false;   // 可选：上报本地 Shepherd 快照，需安装指标文件并手动启用
extern string   HarmonicIndicatorName   = "Market\\Shepherd_Harmonic_Patterns"; // 可配置：需 MQL4/Indicators/Market/Shepherd_Harmonic_Patterns.ex4
extern int      HarmonicPollSeconds     = 10;      // 本地 Shepherd 指标读取间隔（秒，仅启用上方可选项时读取）
extern int      HarmonicLookbackShift   = 1;       // 默认读取已收盘K线
extern int      HarmonicPanelCorner     = 1;       // CORNER_RIGHT_UPPER
extern int      HarmonicPanelX          = 12;
extern int      HarmonicPanelY          = 24;
extern int      HarmonicMaxObjects      = 40;

// ============ 全局变量 ============
datetime lastPollTime    = 0;
datetime lastBarTime     = 0;
datetime lastHistoryTime = 0;   // 上次上报已平仓成交的时间戳（服务端 /api/trade_history）
double   dailyStartEquity = 0;
int      httpTimeout    = 2000;

// ========== 初始化分批发送（避免 OnInit 同步阻塞图表线程）==========
// OnInit 只做 RegisterAccount；心跳 / K线 / 持仓 拆分到 OnTick 首批 tick 内逐项发送
// 0=未开始 1=待发心跳 2=待发持仓 3=待发K线 4=初始数据已全部发送
int      g_initBatchStep = 0;

// ========== 多品种支持 ==========
string   g_symbols[];          // 解析后的品种列表
int      g_symbolCount = 0;    // 品种数量
string   g_ai_symbols[];       // AI 分析品种列表
int      g_ai_symbol_count = 0; // AI 品种数量
string   g_lotMapSymbols[];    // SymbolLotsMap 解析后的品种 key
double   g_lotMapValues[];     // SymbolLotsMap 解析后的手数
int      g_lotMapCount = 0;
string   g_strategyLotMapNames[];   // StrategyLotsMap 解析后的策略 key
double   g_strategyLotMapValues[];  // StrategyLotsMap 解析后的手数
int      g_strategyLotMapCount = 0;

// ========== 连接状态跟踪（v2.8 新增） ==========
bool     gbConnected      = false;        // 当前连接状态
datetime lastSuccessTime  = 0;            // 最后成功通信时间
int      failCount        = 0;            // 连续失败次数
datetime lastRegisterTry  = 0;            // 上次注册尝试时间（每5秒重试）
bool     gbRegistered     = false;        // 注册是否成功
datetime g_lastVisualBridgePollTime = 0;
int      g_visualBridgeSymbolIndex = 0;
int      g_visualBridgeTimeframeIndex = 0;

//+------------------------------------------------------------------+
//| 根据策略名称获取对应的 MagicNumber|
//+------------------------------------------------------------------+
int GetStrategyMagic(string strategy)
{
   if(strategy == "pullback") return PullbackMagic;
   if(strategy == "breakout_retest") return BreakoutMagic;
   if(strategy == "divergence") return DivergenceMagic;
   if(strategy == "breakout_pyramid") return PyramidMagic;
   if(strategy == "counter_pullback") return CounterMagic;
   if(strategy == "range") return RangeMagic;
   if(strategy == "momentum_scalp") return MomentumScalpMagic;
   if(strategy == "ai_signal") return AISignalMagic;
   if(strategy == "scale_in") return ScaleInMagic;
   return 0;
}

//+------------------------------------------------------------------+
bool IsStrategyEnabled(string strategy)
{
   if(strategy == "pullback") return EnablePullback;
   if(strategy == "breakout_retest") return EnableBreakout;
   if(strategy == "divergence") return EnableDivergence;
   if(strategy == "breakout_pyramid") return EnablePyramid;
   if(strategy == "counter_pullback") return EnableCounter;
   if(strategy == "range") return EnableRange;
   if(strategy == "momentum_scalp") return EnableMomentumScalp;
   if(strategy == "ai_signal") return EnableAISignal;
   if(strategy == "scale_in") return EnableScaleIn;
   return false;
}

//+------------------------------------------------------------------+
//| 多品种解析与查询                                                     |
//+------------------------------------------------------------------+
// 解析逗号分隔的品种字符串到数组
void ParseSymbols(string symbolList)
{
   g_symbolCount = 0;
   string remaining = symbolList;
   
   while(StringLen(remaining) > 0)
   {
      // 找逗号
      int pos = StringFind(remaining, ",");
      string token;
      if(pos < 0)
      {
         token = remaining;
         remaining = "";
      }
      else
      {
         token = StringSubstr(remaining, 0, pos);
         remaining = StringSubstr(remaining, pos + 1);
      }
      
      // 去空格
      token = StringTrimLeft(token);
      token = StringTrimRight(token);
      
      if(StringLen(token) > 0)
      {
         ArrayResize(g_symbols, g_symbolCount + 1);
         g_symbols[g_symbolCount] = token;
         g_symbolCount++;
      }
   }
}

// 解析 AI 品种字符串
void ParseAISymbols()
{
   string symbolList = AISymbols;
   symbolList = StringTrimLeft(symbolList);
   symbolList = StringTrimRight(symbolList);

   // 如果 AISymbols 为空，使用交易品种
   if(StringLen(symbolList) == 0)
   {
      g_ai_symbol_count = g_symbolCount;
      ArrayResize(g_ai_symbols, g_ai_symbol_count);
      for(int i = 0; i < g_symbolCount; i++)
      {
         g_ai_symbols[i] = g_symbols[i];
      }
      Print("📋 AI 品种未配置，使用交易品种列表");
      return;
   }

   g_ai_symbol_count = 0;
   ArrayResize(g_ai_symbols, 0);

   while(StringLen(symbolList) > 0)
   {
      int pos = StringFind(symbolList, ",");
      string token;
      if(pos < 0)
      {
         token = symbolList;
         symbolList = "";
      }
      else
      {
         token = StringSubstr(symbolList, 0, pos);
         symbolList = StringSubstr(symbolList, pos + 1);
      }

      token = StringTrimLeft(token);
      token = StringTrimRight(token);

      if(StringLen(token) > 0)
      {
         ArrayResize(g_ai_symbols, g_ai_symbol_count + 1);
         g_ai_symbols[g_ai_symbol_count] = token;
         g_ai_symbol_count++;
      }
   }

   Print("📋 AI 品种解析完成: ", g_ai_symbol_count, " 个品种");
}

// 构建 AI 品种 JSON 数组
string BuildAISymbolsJson()
{
   string json = "[";
   for(int i = 0; i < g_ai_symbol_count; i++)
   {
      if(i > 0) json = json + ",";
      json = json + "\"" + JsonSafeText(g_ai_symbols[i]) + "\"";
   }
   json = json + "]";
   return json;
}

// 获取经纪商品种名称（加后缀）
string GetBrokerSymbol(string baseSymbol)
{
   if(StringLen(SymbolSuffix) == 0)
      return baseSymbol;
   return baseSymbol + SymbolSuffix;
}

// 在数组中查找品种
bool FindSymbolInArray(string sym)
{
   for(int i = 0; i < g_symbolCount; i++)
   {
      if(g_symbols[i] == sym)
         return true;
   }
   return false;
}

// 解析 SymbolLotsMap: "XAUUSD:0.10,US100:0.05"
void ParseSymbolLotsMap(string map)
{
   g_lotMapCount = 0;
   ArrayResize(g_lotMapSymbols, 0);
   ArrayResize(g_lotMapValues, 0);

   string remaining = map;
   remaining = StringTrimLeft(remaining);
   remaining = StringTrimRight(remaining);
   if(StringLen(remaining) == 0)
      return;

   while(StringLen(remaining) > 0)
   {
      int pos = StringFind(remaining, ",");
      string token;
      if(pos < 0)
      {
         token = remaining;
         remaining = "";
      }
      else
      {
         token = StringSubstr(remaining, 0, pos);
         remaining = StringSubstr(remaining, pos + 1);
      }

      token = StringTrimLeft(token);
      token = StringTrimRight(token);
      if(StringLen(token) == 0)
         continue;

      int colon = StringFind(token, ":");
      if(colon <= 0)
      {
         Print("⚠️ SymbolLotsMap 无效项（需 SYMBOL:LOTS）: ", token);
         continue;
      }

      string sym = StringSubstr(token, 0, colon);
      string lotsStr = StringSubstr(token, colon + 1);
      sym = StringTrimLeft(sym);
      sym = StringTrimRight(sym);
      lotsStr = StringTrimLeft(lotsStr);
      lotsStr = StringTrimRight(lotsStr);

      double lots = StringToDouble(lotsStr);
      if(StringLen(sym) == 0 || lots <= 0.0)
      {
         Print("⚠️ SymbolLotsMap 无效项: ", token);
         continue;
      }

      ArrayResize(g_lotMapSymbols, g_lotMapCount + 1);
      ArrayResize(g_lotMapValues, g_lotMapCount + 1);
      g_lotMapSymbols[g_lotMapCount] = sym;
      g_lotMapValues[g_lotMapCount] = lots;
      g_lotMapCount++;
   }
}

// 按品种取固定手数：精确匹配优先，其次最长前缀匹配（US100 → US100Cash）
double GetFixedLotsForSymbol(string symbol, double defaultLots)
{
   if(g_lotMapCount <= 0 || StringLen(symbol) == 0)
      return defaultLots;

   double bestLots = defaultLots;
   int bestLen = -1;
   for(int i = 0; i < g_lotMapCount; i++)
   {
      string key = g_lotMapSymbols[i];
      if(StringLen(key) == 0)
         continue;

      bool matched = false;
      if(symbol == key)
         matched = true;
      else if(StringFind(symbol, key) == 0)
         matched = true;
      else if(StringFind(key, symbol) == 0)
         matched = true;

      if(!matched)
         continue;

      int len = StringLen(key);
      if(len > bestLen)
      {
         bestLen = len;
         bestLots = g_lotMapValues[i];
      }
   }
   return bestLots;
}

string FormatSymbolLotsMap()
{
   if(g_lotMapCount <= 0)
      return "(空，全部用 FixedLots)";

   string out = "";
   for(int i = 0; i < g_lotMapCount; i++)
   {
      if(i > 0) out = out + ", ";
      out = out + g_lotMapSymbols[i] + ":" + DoubleToString(g_lotMapValues[i], 2);
   }
   return out;
}

string NormalizeStrategyName(string strategy)
{
   strategy = StringTrimLeft(strategy);
   strategy = StringTrimRight(strategy);
   StringToLower(strategy);
   return strategy;
}

bool IsKnownStrategyName(string strategy)
{
   strategy = NormalizeStrategyName(strategy);
   if(strategy == "pullback") return true;
   if(strategy == "breakout_retest") return true;
   if(strategy == "divergence") return true;
   if(strategy == "breakout_pyramid") return true;
   if(strategy == "counter_pullback") return true;
   if(strategy == "range") return true;
   if(strategy == "momentum_scalp") return true;
   if(strategy == "ai_signal") return true;
   if(strategy == "scale_in") return true;
   return false;
}

// 解析 StrategyLotsMap: "ai_signal:0.01,pullback:0.10"
void ParseStrategyLotsMap(string map)
{
   g_strategyLotMapCount = 0;
   ArrayResize(g_strategyLotMapNames, 0);
   ArrayResize(g_strategyLotMapValues, 0);

   string remaining = map;
   remaining = StringTrimLeft(remaining);
   remaining = StringTrimRight(remaining);
   if(StringLen(remaining) == 0)
      return;

   while(StringLen(remaining) > 0)
   {
      int pos = StringFind(remaining, ",");
      string token;
      if(pos < 0)
      {
         token = remaining;
         remaining = "";
      }
      else
      {
         token = StringSubstr(remaining, 0, pos);
         remaining = StringSubstr(remaining, pos + 1);
      }

      token = StringTrimLeft(token);
      token = StringTrimRight(token);
      if(StringLen(token) == 0)
         continue;

      int colon = StringFind(token, ":");
      if(colon <= 0)
      {
         Print("⚠️ StrategyLotsMap 无效项（需 STRATEGY:LOTS）: ", token);
         continue;
      }

      string strategy = StringSubstr(token, 0, colon);
      string lotsStr = StringSubstr(token, colon + 1);
      strategy = NormalizeStrategyName(strategy);
      lotsStr = StringTrimLeft(lotsStr);
      lotsStr = StringTrimRight(lotsStr);

      double lots = StringToDouble(lotsStr);
      if(StringLen(strategy) == 0 || lots <= 0.0 || !IsKnownStrategyName(strategy))
      {
         Print("⚠️ StrategyLotsMap 无效项: ", token);
         continue;
      }

      ArrayResize(g_strategyLotMapNames, g_strategyLotMapCount + 1);
      ArrayResize(g_strategyLotMapValues, g_strategyLotMapCount + 1);
      g_strategyLotMapNames[g_strategyLotMapCount] = strategy;
      g_strategyLotMapValues[g_strategyLotMapCount] = lots;
      g_strategyLotMapCount++;
   }
}

double GetFixedLotsForStrategy(string strategy)
{
   if(g_strategyLotMapCount <= 0)
      return 0.0;

   string key = NormalizeStrategyName(strategy);
   if(StringLen(key) == 0)
      return 0.0;

   for(int i = 0; i < g_strategyLotMapCount; i++)
   {
      if(g_strategyLotMapNames[i] == key)
         return g_strategyLotMapValues[i];
   }

   return 0.0;
}

string FormatStrategyLotsMap()
{
   if(g_strategyLotMapCount <= 0)
      return "(空，不覆盖策略手数)";

   string out = "";
   for(int i = 0; i < g_strategyLotMapCount; i++)
   {
      if(i > 0) out = out + ", ";
      out = out + g_strategyLotMapNames[i] + ":" + DoubleToString(g_strategyLotMapValues[i], 2);
   }
   return out;
}

//+------------------------------------------------------------------+
bool IsPrimarySymbol(string sym)
{
   return FindSymbolInArray(sym);
}

//+------------------------------------------------------------------+
bool IsAllowedSymbol(string sym)
{
   return IsPrimarySymbol(sym);
}

//+------------------------------------------------------------------+
bool IsOurMagic(int magic)
{
   if(magic == PullbackMagic) return true;
   if(magic == BreakoutMagic) return true;
   if(magic == DivergenceMagic) return true;
   if(magic == PyramidMagic) return true;
   if(magic == CounterMagic) return true;
   if(magic == RangeMagic) return true;
   if(magic == MomentumScalpMagic) return true;
   if(magic == AISignalMagic) return true;
   if(magic == ScaleInMagic) return true;
   return false;
}

//+------------------------------------------------------------------+
double GetSymbolPoint(string sym)
{
   string brokerSym = GetBrokerSymbol(sym);
   double point = MarketInfo(brokerSym, MODE_POINT);
   if(point <= 0)
      point = Point;

   return point;
}

//+------------------------------------------------------------------+
double GetCurrentSpreadPoints(string sym)
{
   double currentSpread = MarketInfo(sym, MODE_SPREAD);
   if(currentSpread > 0)
      return currentSpread;

   double point = GetSymbolPoint(sym);
   double bid = MarketInfo(sym, MODE_BID);
   double ask = MarketInfo(sym, MODE_ASK);
   if(point <= 0 || bid <= 0 || ask <= 0)
      return -1.0;

   currentSpread = (ask - bid) / point;
   return currentSpread;
}

//+------------------------------------------------------------------+
int GetVolumeDigits(string sym)
{
   double stepLots = MarketInfo(sym, MODE_LOTSTEP);
   if(stepLots <= 0)
      return 2;

   int digits = 0;
   while(digits < 8)
   {
      double rounded = MathRound(stepLots);
      if(MathAbs(stepLots - rounded) < 0.00000001)
         break;

      stepLots *= 10.0;
      digits++;
   }

   return digits;
}

//+------------------------------------------------------------------+
double NormalizeVolume(string sym, double lots)
{
   double minLots  = MarketInfo(sym, MODE_MINLOT);
   double maxLots  = MarketInfo(sym, MODE_MAXLOT);
   double stepLots = MarketInfo(sym, MODE_LOTSTEP);

   if(stepLots <= 0) stepLots = 0.01;
   if(minLots <= 0) minLots = stepLots;
   if(maxLots <= 0) maxLots = lots;

   lots = MathMax(minLots, MathMin(maxLots, lots));
   lots = MathFloor(lots / stepLots) * stepLots;
   return NormalizeDouble(MathMax(minLots, lots), GetVolumeDigits(sym));
}

//+------------------------------------------------------------------+
double NormalizeCloseVolume(string sym, double lots)
{
   double minLots  = MarketInfo(sym, MODE_MINLOT);
   double maxLots  = MarketInfo(sym, MODE_MAXLOT);
   double stepLots = MarketInfo(sym, MODE_LOTSTEP);

   if(stepLots <= 0) stepLots = 0.01;
   if(minLots <= 0) minLots = stepLots;
   if(maxLots <= 0) maxLots = lots;

   lots = MathMax(0.0, MathMin(maxLots, lots));
   double normalizedLots = MathFloor((lots + 0.0000001) / stepLots) * stepLots;
   if(normalizedLots + 0.0000001 < minLots)
      return 0.0;

   return NormalizeDouble(normalizedLots, GetVolumeDigits(sym));
}

//+------------------------------------------------------------------+
int OnInit()
{
   Print("=== Gold Bolt Client v", EA_VERSION, " (Build ", EA_BUILD, ") ===");
   Print("服务器：", ServerURL);
   Print("账户 ID: ", AccountID);
   
   // 解析多品种
   ParseSymbols(Symbols);
   ParseAISymbols();
   ParseSymbolLotsMap(SymbolLotsMap);
   ParseStrategyLotsMap(StrategyLotsMap);
   Print("交易品种(", g_symbolCount, "):");
   for(int s = 0; s < g_symbolCount; s++)
   {
      string brokerSym = GetBrokerSymbol(g_symbols[s]);
      bool avail = IsSymbolAvailable(brokerSym);
      double mappedLots = GetFixedLotsForSymbol(g_symbols[s], FixedLots);
      Print("   ", s+1, ". ", g_symbols[s], " → ", brokerSym, " ", (avail ? "✅" : "❌"),
            " | lots=", DoubleToString(mappedLots, 2));
      if(!avail)
      {
         Print("❌ 品种不可用: ", brokerSym, " | 请检查是否已加入 Market Watch");
         return INIT_FAILED;
      }
   }
   Print("策略Magic: 趋势回调=", PullbackMagic, " 突破回踩=", BreakoutMagic,
         " RSI背离=", DivergenceMagic, " 突破加仓=", PyramidMagic,
         " 反向回调=", CounterMagic, " 震荡区间=", RangeMagic,
         " 动量剥头皮=", MomentumScalpMagic, " AI信号=", AISignalMagic);
   Print("风控：",
         (UseFixedLots ? ("固定手数默认=" + DoubleToString(FixedLots, 2)) : ("风险=" + DoubleToString(MaxRiskPercent, 1) + "%")),
         " | 持仓上限", MaxPositions,
         " | 日亏损", MaxDailyLoss, "% | 浮亏", MaxFloatLoss, "%");
   Print("品种手数映射：", FormatSymbolLotsMap());
   Print("策略手数映射：", FormatStrategyLotsMap());
   Print("动量剥头皮：",
         (EnableMomentumScalp ? "启用" : "禁用"),
         " | ",
         (MomentumScalpUseFixedLots ? ("固定手数=" + DoubleToString(MomentumScalpFixedLots, 2)) : ("风险=" + DoubleToString(MomentumScalpRiskPercent, 1) + "%")));

   // 图表品种检查：允许挂载任意已配置品种的图表
   string chartSym = Symbol();
   if(!FindSymbolInArray(chartSym))
   {
      Print("⚠️ 图表品种 ", chartSym, " 未在配置列表中 | 已配置: ", Symbols);
      Print("   EA 仍可运行，但建议挂载已配置品种的图表以获取最佳报价");
   }

   // 扫描已有持仓（按策略分类）
   Print("📊 扫描已有持仓...");
   int pullbackCount = 0, breakoutCount = 0, divergenceCount = 0;
   int pyramidCount = 0, counterCount = 0, rangeCount = 0, momentumScalpCount = 0, aiSignalCount = 0, scaleInCount = 0;
   
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(!IsAllowedSymbol(OrderSymbol()))
            continue;

         int magic = OrderMagicNumber();
         string type = (OrderType() == OP_BUY ? "BUY" : "SELL");
         string info = OrderSymbol() + " " + type + " " + DoubleToString(OrderLots(), 2) + " 手 | Ticket=" + IntegerToString(OrderTicket());
         
         if(magic == PullbackMagic){ pullbackCount++; Print("   📈 趋势回调: ", info); }
         else if(magic == BreakoutMagic){ breakoutCount++; Print("   🔥 突破回踩: ", info); }
         else if(magic == DivergenceMagic){ divergenceCount++; Print("   📊 RSI背离: ", info); }
         else if(magic == PyramidMagic){ pyramidCount++; Print("   🏗️ 突破加仓: ", info); }
         else if(magic == CounterMagic){ counterCount++; Print("   🔄 反向回调: ", info); }
         else if(magic == RangeMagic){ rangeCount++; Print("   📊 震荡区间: ", info); }
         else if(magic == MomentumScalpMagic){ momentumScalpCount++; Print("   ⚡ 动量剥头皮: ", info); }
         else if(magic == AISignalMagic){ aiSignalCount++; Print("   🤖 AI信号: ", info); }
         else if(magic == ScaleInMagic){ scaleInCount++; Print("   ➕ 加仓: ", info); }
      }
   }
   
   Print("   趋势回调: ", pullbackCount, " 单 | 突破回踩: ", breakoutCount, " 单 | RSI背离: ", divergenceCount, " 单");
   Print("   突破加仓: ", pyramidCount, " 单 | 反向回调: ", counterCount, " 单 | 震荡区间: ", rangeCount, " 单 | 动量剥头皮: ", momentumScalpCount, " 单 | AI信号: ", aiSignalCount, " 单 | 加仓: ", scaleInCount, " 单");
   Print("=============================================");
   
   dailyStartEquity = AccountEquity();

   // 注册账户信息（含 broker 信息），失败时由 OnTick 每 5 秒重试
   // 注意：OnInit 不再同步发送 heartbeat/bars/positions —— 改由 OnTick 首批 tick 内分批发送，
   // 避免挂载瞬间一次性发起 10+ 个同步 WebRequest 阻塞图表线程。
   if(!RegisterAccount())
   {
      gbRegistered = false;
      Print("⚠️ 注册失败，OnTick 将每 5 秒重试...");
   }
   else
   {
      // 标记进入初始化分批阶段，后续由 OnTick 推进
      g_initBatchStep = 1;
      Print("✅ 注册成功，初始数据(心跳/持仓/K线)将由 OnTick 分批发送...");
   }

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime now = TimeCurrent();

    // 首次 tick 提示
    static bool firstTick = false;
    if(!firstTick) { Print("📡 首次 Tick 收到"); firstTick = true; }

    // ========== 初始化分批发送：每个 tick 推进一步，避免一次性阻塞图表 ==========
    // step 1 → SendHeartbeat；step 2 → SendPositions；step 3 → SendAllBars；step 4 → 完成
    // 任意一步失败会在下一个 tick 重试（因为 g_initBatchStep 不会推进到 4）
    if(gbRegistered && g_initBatchStep > 0 && g_initBatchStep < 4)
    {
       if(g_initBatchStep == 1)
       {
          SendHeartbeat();
          g_initBatchStep = 2;
       }
       else if(g_initBatchStep == 2)
       {
          SendPositions();
          g_initBatchStep = 3;
       }
       else if(g_initBatchStep == 3)
       {
          SendAllBars();
          g_initBatchStep = 4;
          lastBarTime = now;   // K 线已发，重置 BarInterval 计时
          Print("✅ 初始数据发送完成");
       }
       // 初次分批期间不再执行后续定时逻辑，让初始化尽快完成
       return;
    }

    // 每 tick 发送报价（包含多品种价格）
    SendTick();

    // ========== v2.8: 注册失败时每 5 秒重试 ==========
    if(!gbRegistered && now - lastRegisterTry >= 5)
    {
       lastRegisterTry = now;
       Print("🔄 尝试注册 GB Server...");
       if(RegisterAccount())
       {
          // 注册成功后进入分批发送流程（与 OnInit 成功路径一致）
          Print("✅ 注册成功，初始数据(心跳/持仓/K线)将由 OnTick 分批发送...");
          g_initBatchStep = 1;
       }
    }

    // 定时：心跳 + 持仓 + 轮询指令（仅注册成功后执行）
    if(gbRegistered && now - lastPollTime >= PollInterval)
    {
       SendHeartbeat();
       SendPositions();
       PollAndExecute();
       CheckForUpdate();
       lastPollTime = now;
    }

    if(gbRegistered)
       PollVisualBridge();

    // 定时发送 K 线（仅注册成功后执行）
    if(gbRegistered && now - lastBarTime >= BarInterval)
    {
       SendAllBars();
       lastBarTime = now;
    }

    // 每 5 分钟上报一次已平仓成交（绩效追踪）
    if(gbRegistered && now - lastHistoryTime >= 300)
    {
       SendTradeHistory();
       lastHistoryTime = now;
    }
   
   // 日切重置
   static int lastDay = 0;
   int today = Day();
   if(today != lastDay)
   {
      dailyStartEquity = AccountEquity();
      lastDay = today;
      Print("📅 日切重置 | 起始权益：", dailyStartEquity);
   }
}

//+------------------------------------------------------------------+
//| 注册账户信息（含 broker 信息，服务端用于识别账户类型）               |
//+------------------------------------------------------------------+
bool RegisterAccount()
{
   string broker = AccountCompany();
   string server = AccountServer();
   string name = AccountName();
   string type = "standard";
   if(StringFind(broker, "ECN") >= 0 || StringFind(server, "ECN") >= 0)
      type = "ecn";
   else if(StringFind(broker, "Pro") >= 0 || StringFind(server, "Pro") >= 0)
      type = "pro";
   
   int leverage = AccountLeverage();
   string currency = AccountCurrency();
   if(StringLen(currency) == 0) currency = "USD";
   string aiSymbolsJson = BuildAISymbolsJson();
   
   string json = StringFormat(
      "{"
      "\"account_id\":\"%s\","
      "\"symbol\":\"%s\","
      "\"magic\":%d,"
      "\"broker\":\"%s\","
      "\"server_name\":\"%s\","
      "\"account_name\":\"%s\","
      "\"account_type\":\"%s\","
      "\"currency\":\"%s\","
      "\"leverage\":%d,"
      "\"max_daily_loss\":%.2f,"
      "\"strategy_mapping\":{"
      "\"pullback\":\"pullback\","
      "\"breakout_retest\":\"breakout_retest\","
      "\"divergence\":\"divergence\","
      "\"breakout_pyramid\":\"breakout_pyramid\","
      "\"counter_pullback\":\"counter_pullback\","
      "\"range\":\"range\","
      "\"momentum_scalp\":\"momentum_scalp\""
      "},"
      "\"ai_symbols\":%s"
      "}",
      AccountID, g_symbols[0], PullbackMagic, broker, server, name, type, currency, leverage,
      MaxDailyLoss, aiSymbolsJson
   );
   
   string resp = HttpPost("/register", json);
   if(StringLen(resp) > 0 && StringFind(resp, "OK") >= 0)
   {
      gbRegistered = true;
      gbConnected = true;
      lastSuccessTime = TimeCurrent();
      failCount = 0;
      Print("📋 账户注册成功 | Broker:", broker, " | Leverage:1:", leverage);
      return true;
   }
   else
   {
      Print("❌ 账户注册失败");
      return false;
   }
}

//+------------------------------------------------------------------+
// 发送心跳（附带账户基础信息）
//+------------------------------------------------------------------+
void SendHeartbeat()
{
   // ========== v2.8: MT4 服务器时间和交易状态 ==========
   string serverTime = TimeToStr(TimeCurrent(), TIME_DATE|TIME_MINUTES);
   bool isTradeAllowed = IsTradeAllowed();
   bool marketOpen = (MarketInfo(GetBrokerSymbol(g_symbols[0]), MODE_TRADEALLOWED) != 0);
   string aiSymbolsJson = BuildAISymbolsJson();

   // 计算各策略的持仓数量
   int pullbackPos = 0, breakoutPos = 0, divergencePos = 0;
   int pyramidPos = 0, counterPos = 0, rangePos = 0, momentumScalpPos = 0, scaleInPos = 0;
   
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(!IsAllowedSymbol(OrderSymbol()))
            continue;

          int m = OrderMagicNumber();
          if(m == PullbackMagic) pullbackPos++;
          else if(m == BreakoutMagic) breakoutPos++;
         else if(m == DivergenceMagic) divergencePos++;
         else if(m == PyramidMagic) pyramidPos++;
         else if(m == CounterMagic) counterPos++;
         else if(m == RangeMagic) rangePos++;
         else if(m == MomentumScalpMagic) momentumScalpPos++;
         else if(m == ScaleInMagic) scaleInPos++;
      }
   }
   
   string json = StringFormat(
      "{"
      "\"account_id\":\"%s\","
      "\"symbol\":\"%s\","
      "\"magic\":%d,"
      "\"balance\":%.2f,"
      "\"equity\":%.2f,"
      "\"margin\":%.2f,"
      "\"free_margin\":%.2f,"
      "\"currency\":\"%s\","
      "\"server_time\":\"%s\","
      "\"market_open\":%s,"
      "\"is_trade_allowed\":%s,"
      "\"max_spread\":%.2f,"
      "\"max_daily_loss\":%.2f,"
      "\"strategies\":{"
      "\"pullback\":{\"enabled\":%s,\"magic\":%d,\"positions\":%d},"
      "\"breakout_retest\":{\"enabled\":%s,\"magic\":%d,\"positions\":%d},"
      "\"divergence\":{\"enabled\":%s,\"magic\":%d,\"positions\":%d},"
      "\"breakout_pyramid\":{\"enabled\":%s,\"magic\":%d,\"positions\":%d},"
      "\"counter_pullback\":{\"enabled\":%s,\"magic\":%d,\"positions\":%d},"
      "\"range\":{\"enabled\":%s,\"magic\":%d,\"positions\":%d},"
      "\"momentum_scalp\":{\"enabled\":%s,\"magic\":%d,\"positions\":%d}"
      "},"
      "\"ai_symbols\":%s"
      "}",
      AccountID, g_symbols[0], PullbackMagic, AccountBalance(), AccountEquity(), 
      AccountMargin(), AccountFreeMargin(), AccountCurrency(), serverTime,
      (marketOpen ? "true" : "false"),
      (isTradeAllowed ? "true" : "false"),
      MaxSpread,
      MaxDailyLoss,
      (EnablePullback ? "true" : "false"), PullbackMagic, pullbackPos,
      (EnableBreakout ? "true" : "false"), BreakoutMagic, breakoutPos,
      (EnableDivergence ? "true" : "false"), DivergenceMagic, divergencePos,
      (EnablePyramid ? "true" : "false"), PyramidMagic, pyramidPos,
      (EnableCounter ? "true" : "false"), CounterMagic, counterPos,
      (EnableRange ? "true" : "false"), RangeMagic, rangePos,
      (EnableMomentumScalp ? "true" : "false"), MomentumScalpMagic, momentumScalpPos,
      aiSymbolsJson,
      (EnableScaleIn ? "true" : "false"), ScaleInMagic, scaleInPos
   );
   
   HttpPost("/heartbeat", json);
}

//+------------------------------------------------------------------+
// 发送实时报价（包含多品种价格）
//+------------------------------------------------------------------+
void SendTick()
{
   static datetime lastSend = 0;
   if(TimeCurrent() - lastSend < 1) return;
   lastSend = TimeCurrent();
   
   // 发送每个品种的报价
   for(int s = 0; s < g_symbolCount; s++)
   {
      string baseSymbol = g_symbols[s];
      string brokerSym = GetBrokerSymbol(baseSymbol);
      double bid = MarketInfo(brokerSym, MODE_BID);
      double ask = MarketInfo(brokerSym, MODE_ASK);
      double spread = GetCurrentSpreadPoints(brokerSym);
      if(spread < 0)
         spread = 0.0;
      
      string json = StringFormat(
         "{"
         "\"account_id\":\"%s\","
         "\"magic\":%d,"
         "\"symbol\":\"%s\","
         "\"bid\":%.5f,"
         "\"ask\":%.5f,"
         "\"spread\":%.3f,"
         "\"max_spread\":%.3f,"
         "\"time\":\"%s\""
         "}",
         AccountID, PullbackMagic, baseSymbol, bid, ask, spread, MaxSpread, TimeToStr(TimeCurrent(), TIME_SECONDS)
      );
      
      HttpPost("/tick", json);
   }
}

//+------------------------------------------------------------------+
// 发送所有 K 线数据（多品种）
//+------------------------------------------------------------------+
void SendAllBars()
{
   string tf_names[] = {"M1","M5","M15","M30","H1","H4","D1"};
   int    tf_periods[] = {PERIOD_M1,PERIOD_M5,PERIOD_M15,PERIOD_M30,PERIOD_H1,PERIOD_H4,PERIOD_D1};
   
   for(int s = 0; s < g_symbolCount; s++)
   {
      for(int t = 0; t < 7; t++)
      {
         SendBars(g_symbols[s], tf_names[t], tf_periods[t]);
      }
   }
}

//+------------------------------------------------------------------+
void SendBars(string baseSymbol, string tf_str, int tf_period)
{
   string brokerSym = GetBrokerSymbol(baseSymbol);
   string bars = "";
   for(int i = BarCount - 1; i >= 0; i--)
   {
      datetime t = iTime(brokerSym, tf_period, i);
      if(t == 0) continue;
      
      double o = iOpen(brokerSym, tf_period, i);
      double h = iHigh(brokerSym, tf_period, i);
      double l = iLow(brokerSym, tf_period, i);
      double c = iClose(brokerSym, tf_period, i);
      int    v = (int)iVolume(brokerSym, tf_period, i);
      
      if(bars != "") bars += ",";
      bars += StringFormat(
         "{\"time\":%d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"volume\":%d}",
         t, o, h, l, c, v
      );
   }
   
   // 使用字符串拼接代替StringFormat，避免MQL4长度限制
   string json = "{\"account_id\":\"" + AccountID + 
                 "\",\"symbol\":\"" + baseSymbol + 
                 "\",\"magic\":" + IntegerToString(PullbackMagic) +
                 ",\"timeframe\":\"" + tf_str + 
                 "\",\"bars\":[" + bars + "]}";
   
   HttpPost("/bars", json);
}

//+------------------------------------------------------------------+
// 发送持仓信息（按品种分别发送）
//+------------------------------------------------------------------+
// 订单类型 → 服务端可读字符串（区分市价仓/挂单）
string OrderTypeToString(int ot)
{
   if(ot == OP_BUY)       return "BUY";
   if(ot == OP_SELL)      return "SELL";
   if(ot == OP_BUYLIMIT)  return "BUY_LIMIT";
   if(ot == OP_BUYSTOP)   return "BUY_STOP";
   if(ot == OP_SELLLIMIT) return "SELL_LIMIT";
   if(ot == OP_SELLSTOP)  return "SELL_STOP";
   return "UNKNOWN";
}

bool IsPendingOrderType(int ot)
{
   return (ot == OP_BUYLIMIT || ot == OP_BUYSTOP || ot == OP_SELLLIMIT || ot == OP_SELLSTOP);
}

// comment: GB_<strategy>_... → strategy 名
// 注意：策略名可能含下划线（ai_signal / breakout_retest / breakout_pyramid / counter_pullback / momentum_scalp）
// 不能只取第一个 '_' 段，否则 GB_ai_signal_S78 → "ai"
string StrategyFromComment(string comment)
{
   if(StringLen(comment) < 4) return "";
   if(StringFind(comment, "GB_") != 0) return "";
   string rest = StringSubstr(comment, 3);

   // 已知策略（长名优先）
   string names[9];
   names[0] = "breakout_pyramid";
   names[1] = "counter_pullback";
   names[2] = "breakout_retest";
   names[3] = "momentum_scalp";
   names[4] = "ai_signal";
   names[5] = "divergence";
   names[6] = "pullback";
   names[7] = "scale_in";
   names[8] = "range";

   for(int i = 0; i < 9; i++)
   {
      string n = names[i];
      int nlen = StringLen(n);
      if(StringLen(rest) < nlen) continue;
      if(StringFind(rest, n) != 0) continue;
      // 精确匹配，或后接 '_' / 数字段（Sxx）
      if(StringLen(rest) == nlen) return n;
      string next = StringSubstr(rest, nlen, 1);
      if(next == "_" || next == "") return n;
   }
   return "";
}

void SendPositions()
{
   // 动态初始化 MagicNumber 数组
   int magics[8];
   magics[0] = PullbackMagic;
   magics[1] = BreakoutMagic;
   magics[2] = DivergenceMagic;
   magics[3] = PyramidMagic;
   magics[4] = CounterMagic;
   magics[5] = RangeMagic;
   magics[6] = MomentumScalpMagic;
   magics[7] = AISignalMagic;
   
   for(int s = 0; s < g_symbolCount; s++)
   {
      string positions = "";
      int count = 0;
      string baseSymbol = g_symbols[s];
      
      for(int i = 0; i < OrdersTotal(); i++)
      {
         if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         {
            // 品种匹配：仅当前品种的持仓/挂单
            string orderSym = OrderSymbol();
            if(orderSym != baseSymbol && orderSym != GetBrokerSymbol(baseSymbol))
               continue;
            
            // 检查是否属于任一策略
            bool isOurOrder = false;
            for(int j = 0; j < 8; j++)
            {
               if(OrderMagicNumber() == magics[j])
               {
                  isOurOrder = true;
                  break;
               }
            }
            if(!isOurOrder) continue;

            int ot = OrderType();
            string typeStr = OrderTypeToString(ot);
            string orderClass = IsPendingOrderType(ot) ? "pending" : "market";
            string strategy = StrategyFromComment(OrderComment());
            
            if(positions != "") positions += ",";
            positions += StringFormat(
               "{\"ticket\":%d,\"symbol\":\"%s\",\"type\":\"%s\",\"order_class\":\"%s\",\"lots\":%.2f,\"open_price\":%.5f,"
               "\"sl\":%.5f,\"tp\":%.5f,\"profit\":%.2f,\"open_time\":%d,\"comment\":\"%s\",\"magic\":%d,\"strategy\":\"%s\"}",
               OrderTicket(), OrderSymbol(),
               typeStr, orderClass,
               OrderLots(), OrderOpenPrice(),
               OrderStopLoss(), OrderTakeProfit(),
               OrderProfit(), OrderOpenTime(), OrderComment(),
               OrderMagicNumber(), strategy
            );
            count++;
         }
      }
      
      string json = StringFormat(
         "{\"account_id\":\"%s\",\"symbol\":\"%s\",\"magic\":%d,\"positions\":[%s]}",
         AccountID, baseSymbol, PullbackMagic, positions
      );

      HttpPost("/positions", json);
   }
}

// ============================================================
// 上报已平仓成交（绩效追踪：服务端 closed_trades 表）
// 每次扫描最近 200 条历史订单，上报 close_time 晚于上次已上报时间的记录。
// 服务端按 (account_id, ticket) 幂等去重，重复上报无副作用。
// ============================================================
datetime g_lastReportedCloseTime = 0;

void SendTradeHistory()
{
   int total = OrdersHistoryTotal();
   if(total <= 0) return;

   string trades = "";
   int count = 0;
   datetime maxCloseTime = g_lastReportedCloseTime;

   // 从最新往回扫，最多 200 条，遇到已上报的即停
   int scanned = 0;
   for(int i = total - 1; i >= 0 && scanned < 200; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) continue;
      scanned++;

      // 只上报市价持仓的平仓记录（跳过挂单删除/余额操作）
      int ot = OrderType();
      if(ot != OP_BUY && ot != OP_SELL) continue;
      if(!IsOurMagic(OrderMagicNumber())) continue;
      if(OrderCloseTime() <= g_lastReportedCloseTime) break; // 已上报过

      string side = (ot == OP_BUY) ? "BUY" : "SELL";
      double netProfit = OrderProfit() + OrderSwap() + OrderCommission();

      if(count > 0) trades += ",";
      trades += StringFormat(
         "{\"account_id\":\"%s\",\"ticket\":%d,\"magic\":%d,\"symbol\":\"%s\","
         "\"side\":\"%s\",\"open_price\":%.5f,\"close_price\":%.5f,\"lots\":%.2f,"
         "\"profit\":%.2f,\"open_time\":\"%s\",\"close_time\":\"%s\"}",
         AccountID, OrderTicket(), OrderMagicNumber(), JsonSafeText(OrderSymbol()),
         side, OrderOpenPrice(), OrderClosePrice(), OrderLots(),
         netProfit,
         TimeToStr(OrderOpenTime(), TIME_DATE|TIME_SECONDS),
         TimeToStr(OrderCloseTime(), TIME_DATE|TIME_SECONDS)
      );
      count++;
      if(OrderCloseTime() > maxCloseTime) maxCloseTime = OrderCloseTime();
   }

   if(count == 0) return;

   string json = "{\"trades\":[" + trades + "]}";
   string resp = HttpPost("/api/trade_history", json);
   if(StringLen(resp) > 0 && StringFind(resp, "OK") >= 0)
   {
      g_lastReportedCloseTime = maxCloseTime;
      Print("📊 已上报平仓成交：", count, " 笔");
   }
}

// ============================================================
// 轮询并执行服务端指令（按品种分别轮询）
// EA 只是执行器，不做任何策略判断
// ============================================================
void PollAndExecute()
{
   for(int s = 0; s < g_symbolCount; s++)
   {
      string baseSymbol = g_symbols[s];
      string json = StringFormat("{\"account_id\":\"%s\",\"symbol\":\"%s\",\"magic\":%d}", AccountID, baseSymbol, PullbackMagic);
      string response = HttpPost("/poll", json);
      
      if(StringLen(response) == 0) continue;
      
      int count = GetJsonInt(response, "count");
      if(count == 0) continue;
      
      Print("📨 [", baseSymbol, "] 收到 ", count, " 条指令");
      string commands_str = GetJsonArraySafe(response, "commands");
      
      for(int i = 0; i < count; i++)
      {
         string cmd = GetArrayElement(commands_str, i);
         if(StringLen(cmd) == 0) continue;

         string action = GetJsonStringSafe(cmd, "action");
         string cmd_id = GetJsonStringSafe(cmd, "command_id");

         if(action == "SIGNAL")
            ExecuteSignal(cmd, cmd_id);
         else if(action == "MODIFY")
            ExecuteModify(cmd, cmd_id);
         else if(action == "CLOSE")
            ExecuteClose(cmd, cmd_id);
         else if(action == "PENDING")
            ExecutePending(cmd, cmd_id);
         else if(action == "CANCEL_PENDING")
            ExecuteCancelPending(cmd, cmd_id);
         else
            Print("未知指令类型：", action);
      }
   }
}

// ============================================================
// 执行开仓信号（风控在本地，策略在服务端）
// ============================================================
void ExecuteSignal(string cmd, string cmd_id)
{
   string orderType = GetJsonStringSafe(cmd, "order_type");
   if(orderType == "BUY_LIMIT" || orderType == "BUY_STOP" ||
      orderType == "SELL_LIMIT" || orderType == "SELL_STOP")
   {
      ExecutePending(cmd, cmd_id);
      return;
   }

   string signalSymbol = GetJsonStringSafe(cmd, "symbol");
   string type_str = GetJsonStringSafe(cmd, "type");
   double sl       = GetJsonDouble(cmd, "sl");
   double tp1      = GetJsonDouble(cmd, "tp1");
   // 兼容 AI 信号的 tp 字段名
   if(tp1 == 0.0) tp1 = GetJsonDouble(cmd, "tp");
   double tp2      = GetJsonDouble(cmd, "tp2");  // Multi-TP 拆单: TP2（远目标）
   bool   tpSplit  = GetJsonBool(cmd, "tp_split"); // Multi-TP 拆单: 服务端标志
   int    score    = GetJsonInt(cmd, "score");
   string strategy = GetJsonStringSafe(cmd, "strategy");
   
   // 确定品种：使用信号自带的品种，否则用第一个配置品种
   string baseSymbol = signalSymbol;
   if(StringLen(baseSymbol) == 0)
      baseSymbol = g_symbols[0];
   
   // 获取经纪商品种名称（用于 MT4 API 调用）
   string brokerSymbol = GetBrokerSymbol(baseSymbol);
   
   Print("📡 信号：", type_str, " | 品种=", baseSymbol, " → ", brokerSymbol,
         " | SL=", sl, " TP=", tp1, " | ", strategy, " 评分:", score);

   if(StringLen(signalSymbol) > 0 && !IsPrimarySymbol(signalSymbol))
   {
      Print("❌ 信号品种不在配置列表：", signalSymbol, " | 已配置: ", Symbols);
      ReportResult(cmd_id, "ERROR", 0, "symbol_mismatch");
      return;
   }

   if(type_str != "BUY" && type_str != "SELL")
   {
      Print("❌ 非法信号方向：", type_str);
      ReportResult(cmd_id, "ERROR", 0, "invalid_type");
      return;
   }

   int magicForOrder = GetStrategyMagic(strategy);
   if(magicForOrder <= 0)
   {
      Print("❌ 未知策略：", strategy);
      ReportResult(cmd_id, "ERROR", 0, "invalid_strategy");
      return;
   }

   if(!IsStrategyEnabled(strategy))
   {
      Print("❌ 策略未启用：", strategy);
      ReportResult(cmd_id, "ERROR", 0, "strategy_disabled");
      return;
   }
   
   // 本地风控
   if(!CheckRisk(type_str))
   {
      ReportResult(cmd_id, "REJECTED", 0, "risk_check_failed");
      return;
   }
   
   // 计算手数
   double price = 0.0;
   int op_type = OP_BUY;
   if(type_str == "BUY")
   {
      op_type = OP_BUY;
      price = MarketInfo(brokerSymbol, MODE_ASK);
   }
   else if(type_str == "SELL")
   {
      op_type = OP_SELL;
      price = MarketInfo(brokerSymbol, MODE_BID);
   }
    
   double sl_distance = MathAbs(price - sl);
   double lots = CalcLotsForStrategy(strategy, baseSymbol, sl_distance);
   lots = NormalizeVolume(brokerSymbol, lots);

   string comment = "GB_" + strategy + "_S" + IntegerToString(score);

   // Multi-TP 拆单: 检查是否需要拆成两个订单
   // 条件: tpSplit==true AND tp2>0 AND tp1!=tp2 (服务端已经保证大部分情况)
   if(tpSplit && tp2 > 0 && MathAbs(tp1 - tp2) > _Point)
   {
      ExecuteOpenWithTPSplit(cmd, cmd_id, brokerSymbol, op_type, lots, price,
                              sl, tp1, tp2, strategy, score, magicForOrder);
      return; // 拆单模式独立处理，不走下面的原逻辑
   }

   int ticket = OrderSend(brokerSymbol, op_type, lots, price, Slippage,
                           0, 0, comment, magicForOrder, 0,
                           type_str == "BUY" ? clrGreen : clrRed);
   
   if(ticket > 0)
   {
      Print("✅ 开仓：#", ticket, " ", type_str, " ", lots, "手 @ ", price, 
            " | Magic=", magicForOrder, " (", strategy, ")");

      if(!OrderSelect(ticket, SELECT_BY_TICKET))
      {
         Print("⚠️ 开仓成交但未能选中订单 #", ticket, "，无法验证保护止损");
         ReportResult(cmd_id, "ERROR", ticket, "position_resolve_incomplete");
         return;
      }
      
      // 检查并设置 TP/SL（兼容 ECN/STP broker）
      double current_sl = OrderStopLoss();
      double current_tp = OrderTakeProfit();
      double open_price = OrderOpenPrice();

      // 如果 TP/SL 未设置，尝试单独设置
      if(current_sl == 0 || current_tp == 0)
      {
         double min_stop = MarketInfo(brokerSymbol, MODE_STOPLEVEL) * GetSymbolPoint(brokerSymbol);
         double final_sl = sl;
         double final_tp = tp1;
         
         // 确保 SL 距离符合要求
         if(min_stop > 0 && MathAbs(open_price - sl) < min_stop)
         {
            if(type_str == "BUY") final_sl = open_price - min_stop;
            else final_sl = open_price + min_stop;
         }
         
         // 确保 TP 距离符合要求
         if(min_stop > 0 && MathAbs(tp1 - open_price) < min_stop)
         {
            if(type_str == "BUY") final_tp = open_price + min_stop;
            else final_tp = open_price - min_stop;
         }
         
         if(final_sl != current_sl || final_tp != current_tp)
         {
            if(OrderModify(ticket, OrderOpenPrice(), final_sl, final_tp, 0, clrYellow))
            {
               Print("📝 开仓后设置 TP/SL: SL=", final_sl, " TP=", final_tp);
            }
            else
            {
               int mod_err = GetLastError();
               Print("⚠️ 开仓成功但保护止损附加失败: #", ticket, " Error#", mod_err);
               ReportResult(cmd_id, "ERROR", ticket, "protection_attach_failed");
               return;
            }
         }

         if(!OrderSelect(ticket, SELECT_BY_TICKET))
         {
            Print("⚠️ 开仓后无法重新选中订单 #", ticket, "，保护状态未确认");
            ReportResult(cmd_id, "ERROR", ticket, "position_resolve_incomplete");
            return;
         }
      }

      if(OrderStopLoss() == 0 || OrderTakeProfit() == 0)
      {
         Print("⚠️ 开仓后保护止损未完整附加: #", ticket);
         ReportResult(cmd_id, "ERROR", ticket, "protection_attach_incomplete");
         return;
      }
       
      ReportResult(cmd_id, "OK", ticket, "");

      // === 加仓后统一修改已有同方向仓位的 SL/TP ===
      double unifiedSL = GetJsonDouble(cmd, "unified_sl");
      if(unifiedSL > 0)
      {
         Print("📐 加仓统一SL: ", unifiedSL, " → 修改已有 ", type_str, " 仓位");
         int synced = 0;
         for(int j = OrdersTotal() - 1; j >= 0; j--)
         {
            if(!OrderSelect(j, SELECT_BY_POS, MODE_TRADES)) continue;
            if(OrderSymbol() != brokerSymbol) continue;
            if(OrderMagicNumber() != magicForOrder) continue;
            if(OrderTicket() == ticket) continue; // 跳过刚开的新仓

            int existingType = OrderType();
            if((type_str == "BUY" && existingType != OP_BUY) ||
               (type_str == "SELL" && existingType != OP_SELL))
               continue;

            double existSL = OrderStopLoss();
            double existTP = OrderTakeProfit();
            double newSL = unifiedSL;
            double newTP = tp1; // 统一 TP1

            // 确保 SL 距离符合 broker 最小要求
            double minDist = MarketInfo(brokerSymbol, MODE_STOPLEVEL) * GetSymbolPoint(brokerSymbol);
            if(minDist > 0)
            {
               if(type_str == "BUY" && MathAbs(OrderOpenPrice() - newSL) < minDist)
                  newSL = OrderOpenPrice() - minDist;
               if(type_str == "SELL" && MathAbs(newSL - OrderOpenPrice()) < minDist)
                  newSL = OrderOpenPrice() + minDist;
            }

            // SL 只能向盈利方向移动（BUY: new >= old, SELL: new <= old）
            bool slOK = false;
            if(type_str == "BUY" && newSL >= existSL)  slOK = true;
            if(type_str == "SELL" && newSL <= existSL)  slOK = true;

            if(slOK && (newSL != existSL || newTP != existTP))
            {
               if(OrderModify(OrderTicket(), OrderOpenPrice(), newSL, newTP, 0, clrYellow))
               {
                  synced++;
                  Print("  ✅ #", OrderTicket(), " SL: ", existSL, "→", newSL, " TP: ", existTP, "→", newTP);
               }
               else
               {
                  Print("  ⚠️ #", OrderTicket(), " 改单失败: ", GetLastError());
               }
            }
         }
         if(synced > 0)
            Print("📐 加仓统一SL完成: 同步 ", synced, " 个仓位");
      }
   }
   else
   {
      int err = GetLastError();
      Print("❌ 开仓失败：Error#", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 执行挂单指令（限价/止损单）
// ============================================================
void ExecutePending(string cmd, string cmd_id)
{
   string signalSymbol = GetJsonStringSafe(cmd, "symbol");
   string type_str     = GetJsonStringSafe(cmd, "type");
   string orderType    = GetJsonStringSafe(cmd, "order_type");
   double entry        = GetJsonDouble(cmd, "entry");
   double sl           = GetJsonDouble(cmd, "sl");
   double tp1          = GetJsonDouble(cmd, "tp1");
   if(tp1 == 0.0) tp1 = GetJsonDouble(cmd, "tp"); // 兼容
   double tp2          = GetJsonDouble(cmd, "tp2");  // Multi-TP 拆单: TP2
   bool   tpSplit      = GetJsonBool(cmd, "tp_split"); // Multi-TP 拆单标志
   int    score        = GetJsonInt(cmd, "score");
   string strategy     = GetJsonStringSafe(cmd, "strategy");
   datetime expiration = (datetime)GetJsonInt(cmd, "expiration");

   // 确定品种
   string baseSymbol = signalSymbol;
   if(StringLen(baseSymbol) == 0)
      baseSymbol = g_symbols[0];

   string brokerSymbol = GetBrokerSymbol(baseSymbol);

   Print("📡 挂单信号：", type_str, " | 品种=", baseSymbol, " → ", brokerSymbol,
         " | 入场=", entry, " SL=", sl, " TP=", tp1, " | ", strategy, " 评分:", score);

   // 验证品种
   if(StringLen(signalSymbol) > 0 && !IsPrimarySymbol(signalSymbol))
   {
      Print("❌ 挂单品种不在配置列表：", signalSymbol, " | 已配置: ", Symbols);
      ReportResult(cmd_id, "ERROR", 0, "symbol_mismatch");
      return;
   }

   // 验证方向
   if(type_str != "BUY" && type_str != "SELL")
   {
      Print("❌ 非法挂单方向：", type_str);
      ReportResult(cmd_id, "ERROR", 0, "invalid_type");
      return;
   }

   // 验证策略及magic
   int magicForOrder = GetStrategyMagic(strategy);
   if(magicForOrder <= 0)
   {
      Print("❌ 未知策略：", strategy);
      ReportResult(cmd_id, "ERROR", 0, "invalid_strategy");
      return;
   }

   if(!IsStrategyEnabled(strategy))
   {
      Print("❌ 策略未启用：", strategy);
      ReportResult(cmd_id, "ERROR", 0, "strategy_disabled");
      return;
   }

   double point = GetSymbolPoint(brokerSymbol);
   if(point <= 0) point = Point;

   // 确定挂单类型
   int pendingType = -1;
   if(orderType == "BUY_LIMIT")
      pendingType = OP_BUYLIMIT;
   else if(orderType == "BUY_STOP")
      pendingType = OP_BUYSTOP;
   else if(orderType == "SELL_LIMIT")
      pendingType = OP_SELLLIMIT;
   else if(orderType == "SELL_STOP")
      pendingType = OP_SELLSTOP;
   else
   {
      // 自动检测：entry 与当前价格的相对位置
      if(type_str == "BUY")
      {
         double ask = MarketInfo(brokerSymbol, MODE_ASK);
         if(entry <= ask + 5 * point)
            pendingType = OP_BUYLIMIT; // 低于或接近市价 → 限价买入
         else
            pendingType = OP_BUYSTOP;   // 高于市价 → 止损买入
      }
      else // SELL
      {
         double bid = MarketInfo(brokerSymbol, MODE_BID);
         if(entry >= bid - 5 * point)
            pendingType = OP_SELLLIMIT; // 高于或接近市价 → 限价卖出
         else
            pendingType = OP_SELLSTOP;  // 低于市价 → 止损卖出
      }
   }

   // 检查重复挂单（同品种、同方向、同magic、价格相近）
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != brokerSymbol) continue;
      if(OrderMagicNumber() != magicForOrder) continue;

      int ot = OrderType();
      bool isBuyPending = (ot == OP_BUYLIMIT || ot == OP_BUYSTOP);
      bool isSellPending = (ot == OP_SELLLIMIT || ot == OP_SELLSTOP);
      bool newIsBuy = (pendingType == OP_BUYLIMIT || pendingType == OP_BUYSTOP);

      if((newIsBuy && isBuyPending) || (!newIsBuy && isSellPending))
      {
         // 检查价格是否过于接近（10 点以内视为重复）
         double existingPrice = OrderOpenPrice();
         double point = GetSymbolPoint(brokerSymbol);
         if(MathAbs(existingPrice - entry) < 10 * point)
         {
            Print("❌ 已有相近价格挂单：", brokerSymbol, " 现有=", existingPrice, " 新=", entry);
            ReportResult(cmd_id, "ERROR", 0, "duplicate_pending");
            return;
         }
      }
   }

   // 计算手数：挂单始终使用 EA 策略配置，不使用服务端 cmd.lots 覆盖。
   double currentPrice = (type_str == "BUY") ? MarketInfo(brokerSymbol, MODE_ASK) : MarketInfo(brokerSymbol, MODE_BID);
   double sl_distance = MathAbs(currentPrice - sl);
   double lots = CalcLotsForStrategy(strategy, baseSymbol, sl_distance);
   lots = NormalizeVolume(brokerSymbol, lots);

   // 挂单同样需要通过本地风控检查（此前 ExecutePending 跳过了 CheckRisk）
   if(!CheckRisk(type_str))
   {
      Print("❌ 风控拒绝挂单：", type_str, " ", baseSymbol);
      ReportResult(cmd_id, "REJECTED", 0, "risk_check_failed");
      return;
   }

   string comment = "GB_" + strategy + "_S" + IntegerToString(score);

   // 设置过期时间：默认24小时
   if(expiration <= 0)
      expiration = TimeCurrent() + 24 * 60 * 60;

   double effectiveTP = tp1;
   bool pendingSplitFallback = false;

   // Multi-TP 拆单: 挂单模式同样支持
   if(tpSplit && tp2 > 0 && MathAbs(tp1 - tp2) > _Point)
   {
      // 拆单挂单: 开两个挂单,每个不同 TP
      double lotsTP1 = 0, lotsTP2 = 0;
      if(SplitLotsForMultiTP(brokerSymbol, lots, lotsTP1, lotsTP2))
      {
         string commentBase = comment;
         int ticketA = OrderSend(brokerSymbol, pendingType, lotsTP1, entry, Slippage,
                                  0, 0, commentBase + "_A", magicForOrder, expiration,
                                  type_str == "BUY" ? clrGreen : clrRed);
         int ticketB = OrderSend(brokerSymbol, pendingType, lotsTP2, entry, Slippage,
                                  0, 0, commentBase + "_B", magicForOrder, expiration,
                                  type_str == "BUY" ? clrGreen : clrRed);

         // 设置 TP/SL
         if(ticketA > 0 && OrderSelect(ticketA, SELECT_BY_TICKET))
         {
            double min_stop = MarketInfo(brokerSymbol, MODE_STOPLEVEL) * GetSymbolPoint(brokerSymbol);
            double final_sl = sl, final_tp = tp1;
            if(min_stop > 0 && MathAbs(entry - sl) < min_stop)
            {
               if(type_str == "BUY") final_sl = entry - min_stop;
               else final_sl = entry + min_stop;
            }
            if(min_stop > 0 && MathAbs(tp1 - entry) < min_stop)
            {
               if(type_str == "BUY") final_tp = entry + min_stop;
               else final_tp = entry - min_stop;
            }
            OrderModify(ticketA, entry, final_sl, final_tp, expiration, clrYellow);
         }
         if(ticketB > 0 && OrderSelect(ticketB, SELECT_BY_TICKET))
         {
            double min_stop = MarketInfo(brokerSymbol, MODE_STOPLEVEL) * GetSymbolPoint(brokerSymbol);
            double final_sl = sl, final_tp = tp2;
            if(min_stop > 0 && MathAbs(entry - sl) < min_stop)
            {
               if(type_str == "BUY") final_sl = entry - min_stop;
               else final_sl = entry + min_stop;
            }
            if(min_stop > 0 && MathAbs(tp2 - entry) < min_stop)
            {
               if(type_str == "BUY") final_tp = entry + min_stop;
               else final_tp = entry - min_stop;
            }
            OrderModify(ticketB, entry, final_sl, final_tp, expiration, clrYellow);
         }

         if(ticketA > 0 && ticketB > 0)
         {
            Print("✅ 拆单挂单成功: TP1=#", ticketA, " (", DoubleToString(lotsTP1, 2), "手) | ",
                  "TP2=#", ticketB, " (", DoubleToString(lotsTP2, 2), "手)");
            ReportResult(cmd_id, "OK", ticketA,
                         "split_pending;A=" + IntegerToString(ticketA) + "_" + DoubleToString(lotsTP1, 2) +
                         ";B=" + IntegerToString(ticketB) + "_" + DoubleToString(lotsTP2, 2));
         }
         else
         {
            Print("⚠️ 拆单挂单部分失败: A=", ticketA, " B=", ticketB);
            ReportResult(cmd_id, "PARTIAL", (ticketA > 0 ? ticketA : ticketB),
                         "split_pending;A=" + IntegerToString(ticketA) + ";B=" + IntegerToString(ticketB));
         }
         return;
      }
      else
      {
         Print("⚠️ 拆单挂单手数无效，退回单挂单 TP2，totalLots=", DoubleToString(lots, 2));
         effectiveTP = tp2;
         pendingSplitFallback = true;
      }
   }

   int ticket = OrderSend(brokerSymbol, pendingType, lots, entry, Slippage,
                          0, 0, comment, magicForOrder, expiration,
                          type_str == "BUY" ? clrGreen : clrRed);

   if(ticket > 0)
   {
      Print("✅ 挂单成功：#", ticket, " ", type_str, " ", lots, "手 @ ", entry,
            " | Magic=", magicForOrder, " (", strategy, ")");

      // 尝试设置 SL/TP（部分经纪商可能拒绝，不影响挂单成功）
      if(sl > 0 || effectiveTP > 0)
      {
         if(OrderSelect(ticket, SELECT_BY_TICKET))
         {
            double final_sl = (sl > 0) ? sl : OrderStopLoss();
            double final_tp = (effectiveTP > 0) ? effectiveTP : OrderTakeProfit();
            if(OrderModify(ticket, entry, final_sl, final_tp, expiration, clrYellow))
            {
               Print("📝 挂单设置SL/TP: SL=", final_sl, " TP=", final_tp);
            }
            else
            {
               int mod_err = GetLastError();
               Print("⚠️ 挂单SL/TP设置失败: #", ticket, " Error#", mod_err);
               // 不影响挂单结果
            }
         }
      }

      ReportResult(cmd_id, "OK", ticket, pendingSplitFallback ? "single_tp2_after_split_invalid" : "");
   }
   else
   {
      int err = GetLastError();
      Print("❌ 挂单失败：Error#", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 取消挂单指令
// ============================================================
void ExecuteCancelPending(string cmd, string cmd_id)
{
   int ticket = (int)GetJsonDouble(cmd, "ticket");
   string reason = GetJsonStringSafe(cmd, "reason");

   if(ticket <= 0)
   {
      Print("❌ 取消挂单：无效ticket");
      ReportResult(cmd_id, "ERROR", 0, "invalid_ticket");
      return;
   }

   if(!OrderSelect(ticket, SELECT_BY_TICKET))
   {
      Print("❌ 取消挂单：找不到订单 #", ticket);
      ReportResult(cmd_id, "ERROR", 0, "order_not_found");
      return;
   }

   int ot = OrderType();
   if(ot != OP_BUYLIMIT && ot != OP_BUYSTOP && ot != OP_SELLLIMIT && ot != OP_SELLSTOP)
   {
      Print("❌ 取消挂单：#", ticket, " 不是挂单（", ot, "）");
      ReportResult(cmd_id, "ERROR", 0, "not_pending");
      return;
   }

   if(OrderDelete(ticket))
   {
      Print("🗑️ 取消挂单：#", ticket, " | ", reason);
      ReportResult(cmd_id, "OK", ticket, "");
   }
   else
   {
      int err = GetLastError();
      Print("❌ 取消挂单失败：#", ticket, " Error#", err);
      ReportResult(cmd_id, "ERROR", ticket, IntegerToString(err));
   }
}

// ============================================================
// 执行改单指令（服务端决定止损止盈值）
// ============================================================
void ExecuteModify(string cmd, string cmd_id)
{
   int    ticket = (int)GetJsonDouble(cmd, "ticket");
   
   // 兼容两种字段名：new_sl（AI 止损）或 sl（传统）
   double sl = GetJsonDouble(cmd, "new_sl");
   if(sl == 0.0) sl = GetJsonDouble(cmd, "sl");
   
   // TP 保持原值，服务端不修改 TP
   double tp = GetJsonDouble(cmd, "tp");
   
   Print("📝 改单：#", ticket, " SL=", sl, " TP=", tp);
   
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
   {
      Print("❌ 未找到订单 #", ticket);
      ReportResult(cmd_id, "ERROR", 0, "order_not_found");
      return;
   }

     if(!IsAllowedSymbol(OrderSymbol()))
     {
        Print("❌ 订单品种不属于本实例：", OrderSymbol());
        ReportResult(cmd_id, "ERROR", 0, "symbol_not_allowed");
        return;
     }

    if(!IsOurMagic(OrderMagicNumber()))
    {
       Print("❌ 订单不属于本 EA：Magic=", OrderMagicNumber());
       ReportResult(cmd_id, "ERROR", 0, "order_not_owned");
       return;
    }
     
   bool result = OrderModify(ticket, OrderOpenPrice(), sl, tp, 0, clrYellow);
   if(result)
   {
      Print("✅ 改单成功");
      ReportResult(cmd_id, "OK", ticket, "");
   }
   else
   {
      int err = GetLastError();
      Print("❌ 改单失败：", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 执行平仓指令
// ============================================================
void ExecuteClose(string cmd, string cmd_id)
{
   int ticket = (int)GetJsonDouble(cmd, "ticket");
   string reason = GetJsonStringSafe(cmd, "reason");
   
   Print("📤 平仓：#", ticket, " | ", reason);
   
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
   {
      Print("❌ 未找到订单 #", ticket);
      ReportResult(cmd_id, "ERROR", 0, "order_not_found");
      return;
   }
    
   string sym = OrderSymbol();
   if(!IsAllowedSymbol(sym))
   {
      Print("❌ 订单品种不属于本实例：", sym);
      ReportResult(cmd_id, "ERROR", 0, "symbol_not_allowed");
      return;
   }

   if(!IsOurMagic(OrderMagicNumber()))
   {
      Print("❌ 订单不属于本 EA：Magic=", OrderMagicNumber());
      ReportResult(cmd_id, "ERROR", 0, "order_not_owned");
      return;
   }

   int ot = OrderType();
   // 挂单必须走 CANCEL_PENDING / OrderDelete，禁止 OrderClose（会 4108）
   if(IsPendingOrderType(ot))
   {
      Print("❌ 平仓拒绝：#", ticket, " 是挂单 ", OrderTypeToString(ot), "，应使用 CANCEL_PENDING");
      ReportResult(cmd_id, "ERROR", 0, "not_market");
      return;
   }
   if(ot != OP_BUY && ot != OP_SELL)
   {
      Print("❌ 平仓拒绝：#", ticket, " 非市价仓 type=", ot);
      ReportResult(cmd_id, "ERROR", 0, "not_market");
      return;
   }

   double closePrice = (ot == OP_BUY) ? MarketInfo(sym, MODE_BID) : MarketInfo(sym, MODE_ASK);

   // 读取服务端指定的平仓手数（用于 TP1/TP2 分批平仓）。
   // 若未指定或指定值 >= 总手数，则全平。
   double cmdLots  = GetJsonDouble(cmd, "lots");
   double totalLots = OrderLots();
   double closeLots;
   if(cmdLots > 0.0009 && cmdLots < totalLots - 0.0001)
      closeLots = NormalizeVolume(sym, cmdLots);  // 部分平仓
   else
      closeLots = totalLots;                      // 全平

   Print("📦 平仓手数：指令=", cmdLots, " 持仓=", totalLots, " 执行=", closeLots);

   bool result = OrderClose(ticket, closeLots, closePrice, Slippage,
                            (ot == OP_BUY) ? clrRed : clrGreen);
   if(result)
   {
      Print("✅ 平仓成功");
      ReportResult(cmd_id, "OK", ticket, "");
   }
   else
   {
      int err = GetLastError();
      Print("❌ 平仓失败：", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 检查本地风控
// ============================================================
bool CheckRisk(string type_str)
{
   double currentSpread = GetCurrentSpreadPoints(Symbol());
   if(currentSpread < 0)
   {
      Print("⚠️ 风控：无法获取有效报价/点差");
      return false;
   }

   if(currentSpread > MaxSpread)
   {
      Print("⚠️ 风控：点差过高 ", DoubleToString(currentSpread, 2), " > ", DoubleToString(MaxSpread, 2));
      return false;
   }

   // 检查同方向持仓数
   int sameDir = 0;
    
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(!IsPrimarySymbol(OrderSymbol())) continue;
         if(!IsOurMagic(OrderMagicNumber())) continue;
          
         if((type_str == "BUY" && OrderType() == OP_BUY) ||
            (type_str == "SELL" && OrderType() == OP_SELL))
            sameDir++;
      }
   }
   if(sameDir >= MaxSameDir)
   {
      Print("⚠️ 风控：同方向持仓达到上限 ", MaxSameDir);
      return false;
   }
   
   // 检查日亏损
   double dailyPnL = AccountEquity() - dailyStartEquity;
   double dailyPnL_pct = (dailyPnL / dailyStartEquity) * 100;
   if(dailyPnL_pct < -MaxDailyLoss)
   {
      Print("⚠️ 风控：日亏损达到 ", DoubleToString(-dailyPnL_pct, 2), "% > ", MaxDailyLoss, "%");
      return false;
   }
   
   // 检查浮亏
   double totalProfit = 0;
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(!IsAllowedSymbol(OrderSymbol()))
            continue;

         if(!IsOurMagic(OrderMagicNumber()))
            continue;

         totalProfit += OrderProfit();
      }
   }
   double floatLoss_pct = (totalProfit / AccountEquity()) * 100;
   if(floatLoss_pct < -MaxFloatLoss)
   {
      Print("⚠️ 风控：浮亏达到 ", DoubleToString(-floatLoss_pct, 2), "% > ", MaxFloatLoss, "%");
      return false;
   }
   
   return true;
}

// ============================================================
// 计算手数（基于固定手数或风险百分比；固定手数可按品种映射）
// ============================================================
// tradeSymbol: 基础品种名（如 XAUUSD / US100），用于 map 查找与 tick 计算
// brokerSymbol: 经纪商实际下单品种（含后缀），用于 volume 规范化
// applySymbolMap: 是否套用 SymbolLotsMap（策略独立手数如 momentum_scalp 应传 false）
double CalcLotsWithConfig(bool useFixedLots, double fixedLots, double riskPercent,
                          double sl_distance, string tradeSymbol, string brokerSymbol,
                          bool applySymbolMap = true)
{
   if(StringLen(brokerSymbol) == 0)
      brokerSymbol = tradeSymbol;
   if(StringLen(brokerSymbol) == 0)
      brokerSymbol = Symbol();
   if(StringLen(tradeSymbol) == 0)
      tradeSymbol = brokerSymbol;

   if(useFixedLots)
   {
      double mapped = fixedLots;
      if(applySymbolMap)
      {
         mapped = GetFixedLotsForSymbol(tradeSymbol, fixedLots);
         if(mapped == fixedLots && tradeSymbol != brokerSymbol)
            mapped = GetFixedLotsForSymbol(brokerSymbol, fixedLots);
      }
      return NormalizeVolume(brokerSymbol, mapped);
   }

   double riskAmount = AccountEquity() * (riskPercent / 100.0);
   double tickValue = MarketInfo(brokerSymbol, MODE_TICKVALUE);
   double tickSize = MarketInfo(brokerSymbol, MODE_TICKSIZE);

   if(tickValue <= 0 || tickSize <= 0 || sl_distance <= 0)
      return NormalizeVolume(brokerSymbol, 0.01);

   double lots = riskAmount / (sl_distance / tickSize * tickValue);
   lots = NormalizeDouble(lots, 2);

   return NormalizeVolume(brokerSymbol, MathMax(0.01, lots));
}

double CalcLots(double sl_distance, string tradeSymbol, string brokerSymbol)
{
   return CalcLotsWithConfig(UseFixedLots, FixedLots, MaxRiskPercent, sl_distance, tradeSymbol, brokerSymbol, true);
}

double CalcLotsForStrategy(string strategy, string symbol, double sl_distance)
{
   string tradeSymbol = symbol;
   string brokerSymbol = GetBrokerSymbol(symbol);
   if(StringLen(tradeSymbol) == 0)
   {
      tradeSymbol = Symbol();
      brokerSymbol = tradeSymbol;
   }

   // momentum_scalp 用策略独立手数，不套用 SymbolLotsMap
   if(strategy == "momentum_scalp")
      return CalcLotsWithConfig(MomentumScalpUseFixedLots, MomentumScalpFixedLots,
                                MomentumScalpRiskPercent, sl_distance, tradeSymbol, brokerSymbol, false);

   double strategyLots = GetFixedLotsForStrategy(strategy);
   if(strategyLots > 0.0)
      return NormalizeVolume(brokerSymbol, strategyLots);

   return CalcLots(sl_distance, tradeSymbol, brokerSymbol);
}

// ============================================================
// 检查品种是否可用
// ============================================================
bool IsSymbolAvailable(string sym)
{
   return (MarketInfo(sym, MODE_BID) > 0);
}

// ============================================================
// 报告指令执行结果给服务端
// ============================================================
void ReportResult(string cmd_id, string result, int ticket, string error)
{
   string json = StringFormat(
      "{\"account_id\":\"%s\",\"command_id\":\"%s\",\"result\":\"%s\",\"ticket\":%d,\"error\":\"%s\"}",
      AccountID, cmd_id, result, ticket, error
   );
   
   HttpPost("/order_result", json);
}

// ============================================================
// HTTP POST 请求
// ============================================================
string HttpPost(string path, string data)
{
   string url = ServerURL + path;
   
   char post_data[];
   StringToCharArray(data, post_data, 0, StringLen(data), CP_UTF8);
   
   char result_data[];
   string request_headers = "Content-Type: application/json\r\n";
   if(ApiToken != "")
      request_headers += "X-API-Token: " + ApiToken + "\r\n";

   string response_headers = "";
    
   int timeout = httpTimeout;
    
   // 重试一次
   int code = WebRequest("POST", url, request_headers, timeout, post_data, result_data, response_headers);
   
   if(code >= 200 && code < 300)
   {
      // 成功
      gbConnected = true;
      lastSuccessTime = TimeCurrent();
      failCount = 0;
      return CharArrayToString(result_data);
   }
   
   // 第一次失败，立即重试一次（不再 Sleep，避免阻塞图表线程；若仍失败由调用方下一 tick 再试）
   response_headers = "";
   code = WebRequest("POST", url, request_headers, timeout, post_data, result_data, response_headers);
   
   if(code >= 200 && code < 300)
   {
      gbConnected = true;
      lastSuccessTime = TimeCurrent();
      failCount = 0;
      return CharArrayToString(result_data);
   }
   
   // 两次都失败
   failCount++;
   if(failCount >= 3 && gbConnected)
   {
      gbConnected = false;
      Print("⚠️ GB Server 断连 | 失败次数：", failCount, " | 路径：", path);
   }
   return "";
}

// ============================================================
// Visual bridge HTTP POST without retry or sleep
// ============================================================
string HttpPostBridge(string path, string data, int timeout)
{
   string url = ServerURL + path;

   char post_data[];
   StringToCharArray(data, post_data, 0, StringLen(data), CP_UTF8);

   char result_data[];
   string request_headers = "Content-Type: application/json\r\n";
   if(ApiToken != "")
      request_headers += "X-API-Token: " + ApiToken + "\r\n";

   string response_headers = "";
   int requestTimeout = timeout;
   if(requestTimeout < 100)
      requestTimeout = 100;

   int code = WebRequest("POST", url, request_headers, requestTimeout, post_data, result_data, response_headers);
   if(code >= 200 && code < 300)
      return CharArrayToString(result_data);

   return "";
}

// ============================================================
// 检查更新
// ============================================================
void CheckForUpdate()
{
   string json = StringFormat("{\"version\":\"%s\",\"build\":%d}", EA_VERSION, EA_BUILD);
   string resp = HttpPost("/version_check", json);
   
   if(StringLen(resp) > 0)
   {
      string latest = GetJsonStringSafe(resp, "latest_version");
      int build = GetJsonInt(resp, "latest_build");
      bool force = GetJsonBool(resp, "force_update");
      
      if(latest != EA_VERSION || build > EA_BUILD)
      {
         Print("📢 发现新版本：", latest, " (Build ", build, ")");
         if(force)
            Print("⚠️ 强制更新，请更新后重启 EA");
      }
   }
}

// ============================================================
// JSON 解析辅助函数
// ============================================================
string GetJsonString(string json, string key)
{
   string pattern = "\"" + key + "\":\"";
   int pos = StringFind(json, pattern);
   if(pos < 0) return "";
   
   int start = pos + StringLen(pattern);
   int end = StringFind(json, "\"", start);
   if(end < 0) return "";
   
   return StringSubstr(json, start, end - start);
}

double GetJsonDouble(string json, string key)
{
   string pattern = "\"" + key + "\":";
   int pos = StringFind(json, pattern);
   if(pos < 0) return 0;
   
   int start = pos + StringLen(pattern);
   string rest = StringSubstr(json, start);
   
   // 手动解析数字 (替代 sscanf)
   string num_str = "";
   for(int i = 0; i < StringLen(rest); i++)
   {
      ushort c = StringGetChar(rest, i);
      // 数字、小数点、负号、科学计数法
      if((c >= 48 && c <= 57) || c == 46 || c == 45 || c == 101 || c == 69 || c == 43)
         num_str += ShortToString(c);
      else if(StringLen(num_str) > 0)
         break;
   }
   
   if(StringLen(num_str) == 0) return 0;
   return StringToDouble(num_str);
}

int GetJsonInt(string json, string key)
{
   return (int)GetJsonDouble(json, key);
}

bool GetJsonBool(string json, string key)
{
   string pattern = "\"" + key + "\":";
   int pos = StringFind(json, pattern);
   if(pos < 0) return false;

   int start = pos + StringLen(pattern);
   string rest = StringSubstr(json, start, 5);
   return (StringSubstr(rest, 0, 4) == "true");
}

// ============================================================
// Multi-TP 拆单辅助函数
// ============================================================

// 拆分手数：60% 给 TP1（近目标，先落袋），40% 给 TP2（远目标，剩余）
// 输入：totalLots - 服务端下发的总手数
// 输出：通过引用返回 lotsTP1 和 lotsTP2
// 约束：子手数必须有效，且 lotsTP1 + lotsTP2 不能超过 totalLots 容忍范围
bool SplitLotsForMultiTP(string brokerSymbol, double totalLots, double &lotsTP1, double &lotsTP2)
{
   lotsTP1 = 0;
   lotsTP2 = 0;

   if(totalLots <= 0)
      return false;

   double minLots  = MarketInfo(brokerSymbol, MODE_MINLOT);
   double stepLots = MarketInfo(brokerSymbol, MODE_LOTSTEP);
   if(stepLots <= 0) stepLots = 0.01;
   if(minLots <= 0) minLots = stepLots;

   double tolerance = MathMax(0.0000001, stepLots * 0.0001);
   double normalizedTotal = NormalizeVolume(brokerSymbol, totalLots);
   if(normalizedTotal > totalLots + tolerance)
      return false;
   if(normalizedTotal + tolerance < 2.0 * minLots)
      return false;

   int digits = GetVolumeDigits(brokerSymbol);
   lotsTP1 = NormalizeDouble(MathFloor((normalizedTotal * 0.6 + tolerance) / stepLots) * stepLots, digits);
   lotsTP2 = NormalizeDouble(MathFloor((normalizedTotal * 0.4 + tolerance) / stepLots) * stepLots, digits);

   double remaining = normalizedTotal - lotsTP1 - lotsTP2;
   int remainingSteps = (int)MathFloor((remaining + tolerance) / stepLots);
   if(remainingSteps > 0)
   {
      lotsTP1 = NormalizeDouble(lotsTP1 + remainingSteps * stepLots, digits);
      remaining = normalizedTotal - lotsTP1 - lotsTP2;
   }

   if(lotsTP1 + tolerance < minLots || lotsTP2 + tolerance < minLots)
   {
      lotsTP1 = 0;
      lotsTP2 = 0;
      return false;
   }

   // 安全检查：确保总和不超过规范化总手数的容忍范围
   if(lotsTP1 + lotsTP2 > normalizedTotal + tolerance)
   {
      lotsTP1 = 0;
      lotsTP2 = 0;
      return false;
   }

   return true;
}

bool IsBuyOrderType(int op_type)
{
   return (op_type == OP_BUY || op_type == OP_BUYLIMIT || op_type == OP_BUYSTOP);
}

// 单一订单开仓 + 设置 TP/SL（拆单模式使用）
// 返回：true=成功, ticket 写入 outTicket
bool OpenSingleOrderWithTP(string brokerSymbol, int op_type, double lots, double price,
                           double sl, double tp, string comment, int magic,
                           string strategy, int score, datetime expiration, int &outTicket)
{
   outTicket = 0;
   double inputLots = lots;
   lots = NormalizeVolume(brokerSymbol, lots);
   if(lots > inputLots + 0.0001) return false;
   if(lots <= 0) return false;

   bool isBuy = IsBuyOrderType(op_type);
   int ticket = OrderSend(brokerSymbol, op_type, lots, price, Slippage,
                          0, 0, comment, magic, expiration,
                          isBuy ? clrGreen : clrRed);
   if(ticket <= 0)
   {
      Print("❌ 拆单开仓失败 (", strategy, "): Error#", GetLastError());
      return false;
   }

   Print("✅ 下单: #", ticket, " ", (isBuy ? "BUY" : "SELL"),
         " ", DoubleToString(lots, 2), "手 @ ", DoubleToString(price, Digits),
         " | SL=", DoubleToString(sl, Digits), " TP=", DoubleToString(tp, Digits),
         " | Magic=", magic, " (", strategy, ")");

   // 设置 TP/SL（兼容 ECN/STP broker）
   if(!OrderSelect(ticket, SELECT_BY_TICKET))
   {
      outTicket = ticket;
      return false; // 订单开了但无法设置保护
   }

   double min_stop = MarketInfo(brokerSymbol, MODE_STOPLEVEL) * GetSymbolPoint(brokerSymbol);
   double openPrice = OrderOpenPrice();
   double final_sl = sl;
   double final_tp = tp;

   if(min_stop > 0 && MathAbs(openPrice - sl) < min_stop)
   {
      if(op_type == OP_BUY) final_sl = openPrice - min_stop;
      else final_sl = openPrice + min_stop;
   }
   if(min_stop > 0 && MathAbs(tp - openPrice) < min_stop)
   {
      if(op_type == OP_BUY) final_tp = openPrice + min_stop;
      else final_tp = openPrice - min_stop;
   }

   if(OrderStopLoss() != final_sl || OrderTakeProfit() != final_tp)
   {
      if(!OrderModify(ticket, openPrice, final_sl, final_tp, expiration, clrYellow))
      {
         Print("⚠️ 拆单 TP/SL 设置失败: #", ticket, " Error#", GetLastError());
      }
   }

   outTicket = ticket;
   return true;
}

// 执行拆单开仓（Multi-TP 策略）
// 创建两个订单: 订单A (40% lots @ TP1), 订单B (60% lots @ TP2)
void ExecuteOpenWithTPSplit(string cmd, string cmd_id, string brokerSymbol, int op_type,
                           double lots, double price, double sl,
                           double tp1, double tp2, string strategy,
                           int score, int magicForOrder)
{
   string commentBase = "GB_" + strategy + "_S" + IntegerToString(score);

   // 拆分手数
   double lotsTP1 = 0, lotsTP2 = 0;
   if(!SplitLotsForMultiTP(brokerSymbol, lots, lotsTP1, lotsTP2))
   {
      Print("⚠️ 拆单手数无效，退回单订单 TP2，totalLots=", DoubleToString(lots, 2));
      int fallbackTicket = 0;
      bool fallbackOK = OpenSingleOrderWithTP(brokerSymbol, op_type, lots, price,
                                              sl, tp2, commentBase, magicForOrder,
                                              strategy, score, 0, fallbackTicket);
      if(fallbackOK)
         ReportResult(cmd_id, "OK", fallbackTicket, "single_tp2_after_split_invalid");
      else
         ReportResult(cmd_id, "ERROR", fallbackTicket, "single_tp2_after_split_failed");
      return;
   }

   // 订单 A: TP1（近目标）
   int ticketA = 0;
   bool okA = OpenSingleOrderWithTP(brokerSymbol, op_type, lotsTP1, price,
                                     sl, tp1, commentBase + "_A", magicForOrder,
                                     strategy, score, 0, ticketA);

   // 订单 B: TP2（远目标）
   int ticketB = 0;
   bool okB = OpenSingleOrderWithTP(brokerSymbol, op_type, lotsTP2, price,
                                     sl, tp2, commentBase + "_B", magicForOrder,
                                     strategy, score, 0, ticketB);

   // 报告结果
   if(okA && okB)
   {
      Print("✅ 拆单成功: TP1=#", ticketA, " (", DoubleToString(lotsTP1, 2), "手) | ",
            "TP2=#", ticketB, " (", DoubleToString(lotsTP2, 2), "手) | ",
            "合计=", DoubleToString(lotsTP1 + lotsTP2, 2), "手 / ", DoubleToString(lots, 2), "手");
      ReportResult(cmd_id, "OK", ticketA,
                   "split;A=" + IntegerToString(ticketA) + "_" + DoubleToString(lotsTP1, 2) +
                   ";B=" + IntegerToString(ticketB) + "_" + DoubleToString(lotsTP2, 2));
   }
   else if(okA && !okB)
   {
      Print("⚠️ 拆单部分成功: 订单A=#", ticketA, " (TP1) 成功，订单B 失败");
      ReportResult(cmd_id, "PARTIAL", ticketA,
                   "split;A_ok=" + IntegerToString(ticketA) + ";B_failed_err=" + IntegerToString(GetLastError()));
   }
   else if(!okA && okB)
   {
      Print("⚠️ 拆单部分成功: 订单A 失败，订单B=#", ticketB, " (TP2) 成功");
      ReportResult(cmd_id, "PARTIAL", ticketB,
                   "split;A_failed;B_ok=" + IntegerToString(ticketB));
   }
   else
   {
      Print("❌ 拆单全部失败: 订单A 和 订单B 均未成交");
      ReportResult(cmd_id, "ERROR", 0, "split_all_failed");
   }
}

string GetJsonArray(string json, string key)
{
   string pattern = "\"" + key + "\":[";
   int pos = StringFind(json, pattern);
   if(pos < 0) return "";
   
   int start = pos + StringLen(pattern) - 1;
   int bracket_count = 0;
   int end = start;
   
   for(int i = start; i < StringLen(json); i++)
   {
      if(StringGetChar(json, i) == '[') bracket_count++;
      else if(StringGetChar(json, i) == ']')
      {
         bracket_count--;
         if(bracket_count == 0)
         {
            end = i;
            break;
         }
      }
   }
   
   return StringSubstr(json, start + 1, end - start - 1);
}

string GetArrayElement(string array_str, int index)
{
   int brace_count = 0;
   int start = -1;
   int current_index = 0;
   
   for(int i = 0; i < StringLen(array_str); i++)
   {
      ushort c = StringGetChar(array_str, i);
      if(c == '{')
      {
         if(brace_count == 0) start = i;
         brace_count++;
      }
      else if(c == '}')
      {
         brace_count--;
         if(brace_count == 0 && current_index == index)
            return StringSubstr(array_str, start, i - start + 1);
      }
      else if(c == ',' && brace_count == 0)
      {
         current_index++;
      }
   }
   
   return "";
}

//+------------------------------------------------------------------+
//| 安全的 JSON 字符串解析（处理转义）                                |
//+------------------------------------------------------------------+
string GetJsonStringSafe(string json, string key)
{
   string pattern = "\"" + key + "\":\"";
   int pos = StringFind(json, pattern);
   if(pos < 0) return "";

   int start = pos + StringLen(pattern);
   string result = "";
   bool escaped = false;

   for(int i = start; i < StringLen(json); i++)
   {
      ushort c = StringGetChar(json, i);

      if(escaped)
      {
         // 处理转义字符
         if(c == 'n') result += "\n";
         else if(c == 't') result += "\t";
         else if(c == 'r') result += "\r";
         else if(c == '\\') result += "\\";
         else if(c == '"') result += "\"";
         else result += ShortToString(c);  // 未知转义，保留原字符

         escaped = false;
      }
      else if(c == '\\')
      {
         escaped = true;
      }
      else if(c == '"')
      {
         break;  // 字符串结束
      }
      else
      {
         result += ShortToString(c);
      }
   }

   return result;
}

//+------------------------------------------------------------------+
//| 安全的 JSON 数组解析（忽略字符串内的括号）                        |
//+------------------------------------------------------------------+
string GetJsonArraySafe(string json, string key)
{
   string pattern = "\"" + key + "\":[";
   int pos = StringFind(json, pattern);
   if(pos < 0) return "";

   int start = pos + StringLen(pattern) - 1;
   int bracket_count = 0;
   int end = start;
   bool in_string = false;
   bool escaped = false;

   for(int i = start; i < StringLen(json); i++)
   {
      ushort c = StringGetChar(json, i);

      if(escaped)
      {
         escaped = false;
         continue;
      }

      if(c == '\\')
      {
         escaped = true;
         continue;
      }

      if(c == '"')
      {
         in_string = !in_string;
         continue;
      }

      if(in_string) continue;  // 忽略字符串内的字符

      if(c == '[') bracket_count++;
      else if(c == ']')
      {
         bracket_count--;
         if(bracket_count == 0)
         {
            end = i;
            break;
         }
      }
   }

   return StringSubstr(json, start + 1, end - start - 1);
}

//+------------------------------------------------------------------+
//| Indicator Alert Visual Functions                                   |
//| 图表上绘制背离/谐波信号                                            |
//+------------------------------------------------------------------+

// 全局变量：跟踪已创建的对象数量
int g_indicatorObjectCount = 0;
string g_indicatorObjectPrefix = "GB_Alert_";
int      g_harmonicObjectCount = 0;
string   g_harmonicObjectPrefix = "GB_Harmonic_";
string   g_lastHarmonicReportKey = "";
datetime g_lastHarmonicPollTime = 0;
datetime g_lastHarmonicMissingLogTime = 0;
string   g_lastHarmonicReportState = "Idle";

//+------------------------------------------------------------------+
//| 谐波辅助函数                                                       |
//+------------------------------------------------------------------+
bool HasIndicatorValue(double v)
{
   return (v != EMPTY_VALUE && v != 0.0);
}

//+------------------------------------------------------------------+
string TimeframeToString(int tf)
{
   if(tf == PERIOD_M1) return "M1";
   if(tf == PERIOD_M5) return "M5";
   if(tf == PERIOD_M15) return "M15";
   if(tf == PERIOD_M30) return "M30";
   if(tf == PERIOD_H1) return "H1";
   if(tf == PERIOD_H4) return "H4";
   if(tf == PERIOD_D1) return "D1";
   if(tf == PERIOD_W1) return "W1";
   if(tf == PERIOD_MN1) return "MN1";
   return IntegerToString(tf);
}

//+------------------------------------------------------------------+
string SanitizeVisualFilePart(string value)
{
   string sanitized = StringTrimLeft(StringTrimRight(value));
   string disallowed = "\\/:*?\"<>| ,;";
   for(int i = 0; i < StringLen(disallowed); i++)
   {
      string ch = StringSubstr(disallowed, i, 1);
      StringReplace(sanitized, ch, "_");
   }

   if(StringLen(sanitized) == 0)
      sanitized = "na";

   return sanitized;
}

//+------------------------------------------------------------------+
string VisualCacheFileName(string accountID, string symbol, string timeframe)
{
   return "GoldBoltVisual_" +
          SanitizeVisualFilePart(accountID) + "_" +
          SanitizeVisualFilePart(symbol) + "_" +
          SanitizeVisualFilePart(timeframe) + ".json";
}

//+------------------------------------------------------------------+
bool WriteVisualCache(string fileName, string payload)
{
   int flags = FILE_WRITE | FILE_TXT | FILE_ANSI;
   if(VisualBridgeCommonFiles)
      flags |= FILE_COMMON;

   int handle = FileOpen(fileName, flags);
   if(handle == INVALID_HANDLE)
   {
      Print("⚠️ Visual cache open failed: ", fileName, " error=", GetLastError());
      return false;
   }

   FileWriteString(handle, payload, StringLen(payload));
   FileClose(handle);
   return true;
}

//+------------------------------------------------------------------+
int ParseVisualBridgeTimeframes(string &frames[])
{
   ArrayResize(frames, 0);
   string remaining = VisualBridgeTimeframes;
   int count = 0;

   while(StringLen(remaining) > 0)
   {
      int pos = StringFind(remaining, ",");
      string token;
      if(pos < 0)
      {
         token = remaining;
         remaining = "";
      }
      else
      {
         token = StringSubstr(remaining, 0, pos);
         remaining = StringSubstr(remaining, pos + 1);
      }

      token = StringTrimLeft(StringTrimRight(token));
      if(StringLen(token) == 0)
         continue;

      ArrayResize(frames, count + 1);
      frames[count] = token;
      count++;
   }

   return count;
}

//+------------------------------------------------------------------+
void PollVisualBridge()
{
   if(!EnableVisualBridge || !gbRegistered)
      return;
   if(g_symbolCount <= 0)
      return;

   int pollSeconds = VisualBridgePollSeconds;
   if(pollSeconds < 1) pollSeconds = 1;

   datetime now = TimeCurrent();
   if(g_lastVisualBridgePollTime != 0 && now - g_lastVisualBridgePollTime < pollSeconds)
      return;
   g_lastVisualBridgePollTime = now;

   string timeframes[];
   int timeframeCount = ParseVisualBridgeTimeframes(timeframes);
   if(timeframeCount <= 0)
      return;

   if(g_visualBridgeSymbolIndex < 0 || g_visualBridgeSymbolIndex >= g_symbolCount)
      g_visualBridgeSymbolIndex = 0;
   if(g_visualBridgeTimeframeIndex < 0 || g_visualBridgeTimeframeIndex >= timeframeCount)
      g_visualBridgeTimeframeIndex = 0;

   string baseSymbol = g_symbols[g_visualBridgeSymbolIndex];
   string timeframe = timeframes[g_visualBridgeTimeframeIndex];

   string json = "{";
   json += "\"account_id\":\"" + JsonSafeText(AccountID) + "\",";
   json += "\"symbol\":\"" + JsonSafeText(baseSymbol) + "\",";
   json += "\"timeframe\":\"" + JsonSafeText(timeframe) + "\",";
   json += "\"client\":\"mt4_visual_bridge\"";
   json += "}";

   string response = HttpPostBridge("/visual/poll", json, VisualBridgeTimeoutMs);
   if(StringLen(response) > 0)
   {
      string fileName = VisualCacheFileName(AccountID, baseSymbol, timeframe);
      WriteVisualCache(fileName, response);
   }

   g_visualBridgeTimeframeIndex++;
   if(g_visualBridgeTimeframeIndex >= timeframeCount)
   {
      g_visualBridgeTimeframeIndex = 0;
      g_visualBridgeSymbolIndex++;
      if(g_visualBridgeSymbolIndex >= g_symbolCount)
         g_visualBridgeSymbolIndex = 0;
   }
}

//+------------------------------------------------------------------+
datetime ParseAlertTime(string timeStr)
{
   string raw = StringTrimLeft(StringTrimRight(timeStr));
   if(StringLen(raw) == 0) return 0;

   bool digitsOnly = true;
   for(int i = 0; i < StringLen(raw); i++)
   {
      ushort c = StringGetChar(raw, i);
      if(c < '0' || c > '9')
      {
         digitsOnly = false;
         break;
      }
   }
   if(digitsOnly) return (datetime)StrToInteger(raw);

   string normalized = raw;
   int tzPos = StringFind(normalized, "+", 10);
   if(tzPos < 0)
   {
      for(int j = 10; j < StringLen(normalized); j++)
      {
         if(StringGetChar(normalized, j) == '-')
         {
            tzPos = j;
            break;
         }
      }
   }
   if(tzPos > 0)
      normalized = StringSubstr(normalized, 0, tzPos);

   StringReplace(normalized, "T", " ");
   StringReplace(normalized, "Z", "");
   StringReplace(normalized, "/", ".");
   StringReplace(normalized, "-", ".");

   return StrToTime(StringTrimRight(normalized));
}

//+------------------------------------------------------------------+
string JsonSafeText(string text)
{
   string safe = text;
   StringReplace(safe, "\\", "\\\\");
   StringReplace(safe, "\"", "\\\"");
   StringReplace(safe, "\r", "\\r");
   StringReplace(safe, "\n", "\\n");
   StringReplace(safe, "\t", "\\t");
   return safe;
}

//+------------------------------------------------------------------+
void CleanOldHarmonicObjects()
{
   int removed = 0;
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, g_harmonicObjectPrefix) == 0)
      {
         if(ObjectDelete(0, name))
            removed++;
      }
   }
   g_harmonicObjectCount = 0;
   if(removed > 0)
      Print("🧹 清理谐波对象: ", removed);
}

//+------------------------------------------------------------------+
void UpdateHarmonicPanel(string symbol, string timeframe, string signalState, string divergenceState,
                         bool hasPriceAction, double slValue, double tp1Value, double tp2Value,
                         double tp3Value, string reportState)
{
   string panelName = g_harmonicObjectPrefix + "Panel";
   if(ObjectFind(0, panelName) < 0)
   {
      if(!ObjectCreate(0, panelName, OBJ_LABEL, 0, 0, 0))
         return;
      ObjectSetInteger(0, panelName, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, panelName, OBJPROP_SELECTED, false);
      ObjectSetString(0, panelName, OBJPROP_FONT, "Tahoma");
   }

   color panelColor = clrSilver;
   if(signalState == "bullish")
      panelColor = clrDeepSkyBlue;
   else if(signalState == "bearish")
      panelColor = clrTomato;
   else if(signalState == "Price action only")
      panelColor = clrKhaki;

   string text = "GoldBolt Harmonic\n";
   text += symbol + " / " + timeframe + "\n";
   text += "Signal: " + signalState + "\n";
   text += "Divergence: " + divergenceState + "\n";
   text += "PA: " + (hasPriceAction ? "true" : "false") + "\n";
   if(slValue != EMPTY_VALUE && slValue > 0) text += "SL: " + DoubleToString(slValue, Digits) + "\n";
   if(tp1Value != EMPTY_VALUE && tp1Value > 0) text += "TP1: " + DoubleToString(tp1Value, Digits) + "\n";
   if(tp2Value != EMPTY_VALUE && tp2Value > 0) text += "TP2: " + DoubleToString(tp2Value, Digits) + "\n";
   if(tp3Value != EMPTY_VALUE && tp3Value > 0) text += "TP3: " + DoubleToString(tp3Value, Digits) + "\n";
   text += "Report: " + reportState;

   ObjectSetInteger(0, panelName, OBJPROP_CORNER, HarmonicPanelCorner);
   ObjectSetInteger(0, panelName, OBJPROP_XDISTANCE, HarmonicPanelX);
   ObjectSetInteger(0, panelName, OBJPROP_YDISTANCE, HarmonicPanelY);
   ObjectSetInteger(0, panelName, OBJPROP_COLOR, panelColor);
   ObjectSetInteger(0, panelName, OBJPROP_FONTSIZE, 9);
   ObjectSetString(0, panelName, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
void DrawHarmonicSignalArrow(datetime signalTime, double signalPrice, string direction, string sourceTag)
{
   if(signalPrice <= 0) return;

   string objName = g_harmonicObjectPrefix + "Signal_" + sourceTag + "_" + direction + "_" + IntegerToString((int)signalTime);
   bool exists = (ObjectFind(0, objName) >= 0);
   if(!exists)
   {
      if(!ObjectCreate(0, objName, OBJ_ARROW, 0, signalTime, signalPrice))
         return;
      g_harmonicObjectCount++;
   }

   color arrowColor = (direction == "bullish") ? clrDeepSkyBlue : clrTomato;
   int arrowCode = (direction == "bullish") ? 233 : 234;
   int anchor = (direction == "bullish") ? ANCHOR_BOTTOM : ANCHOR_TOP;

   ObjectMove(0, objName, 0, signalTime, signalPrice);
   ObjectSetInteger(0, objName, OBJPROP_ARROWCODE, arrowCode);
   ObjectSetInteger(0, objName, OBJPROP_COLOR, arrowColor);
   ObjectSetInteger(0, objName, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, objName, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, objName, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, objName, OBJPROP_SELECTED, false);

   int maxObjects = HarmonicMaxObjects;
   if(maxObjects < 5) maxObjects = 5;
   if(g_harmonicObjectCount > maxObjects)
      CleanOldHarmonicObjects();
}

//+------------------------------------------------------------------+
void DrawHarmonicDivergenceMarker(datetime signalTime, double signalPrice, string direction)
{
   if(signalPrice <= 0) return;

   string objName = g_harmonicObjectPrefix + "Div_" + direction + "_" + IntegerToString((int)signalTime);
   bool exists = (ObjectFind(0, objName) >= 0);
   if(!exists)
   {
      if(!ObjectCreate(0, objName, OBJ_ARROW, 0, signalTime, signalPrice))
         return;
      g_harmonicObjectCount++;
   }

   color markerColor = (direction == "bullish") ? clrYellow : clrOrange;
   int arrowCode = (direction == "bullish") ? 241 : 242;
   int anchor = (direction == "bullish") ? ANCHOR_BOTTOM : ANCHOR_TOP;

   ObjectMove(0, objName, 0, signalTime, signalPrice);
   ObjectSetInteger(0, objName, OBJPROP_ARROWCODE, arrowCode);
   ObjectSetInteger(0, objName, OBJPROP_COLOR, markerColor);
   ObjectSetInteger(0, objName, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, objName, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, objName, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, objName, OBJPROP_SELECTED, false);

   int maxObjects = HarmonicMaxObjects;
   if(maxObjects < 5) maxObjects = 5;
   if(g_harmonicObjectCount > maxObjects)
      CleanOldHarmonicObjects();
}

//+------------------------------------------------------------------+
void DrawHarmonicLevelLine(string levelName, double levelValue, color levelColor)
{
   string objName = g_harmonicObjectPrefix + levelName;
   if(levelValue == EMPTY_VALUE || levelValue <= 0)
   {
      if(ObjectFind(0, objName) >= 0)
         ObjectDelete(0, objName);
      return;
   }

   if(ObjectFind(0, objName) < 0)
   {
      if(!ObjectCreate(0, objName, OBJ_HLINE, 0, 0, levelValue))
         return;
   }

   ObjectSetDouble(0, objName, OBJPROP_PRICE, levelValue);
   ObjectSetInteger(0, objName, OBJPROP_COLOR, levelColor);
   ObjectSetInteger(0, objName, OBJPROP_STYLE, STYLE_DASH);
   ObjectSetInteger(0, objName, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, objName, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, objName, OBJPROP_SELECTED, false);
}

//+------------------------------------------------------------------+
void ReportHarmonicSnapshot(string symbol, string timeframe, string direction, datetime signalTime,
                            double signalPrice, double slValue, double tp1Value, double tp2Value,
                            double tp3Value, bool hasPriceAction)
{
   if(!EnableHarmonicReport) return;
   if(direction != "bullish" && direction != "bearish") return;

   string reportKey = symbol + "_" + timeframe + "_" + direction + "_" +
                      IntegerToString((int)signalTime) + "_" + DoubleToString(signalPrice, Digits);

   if(reportKey == g_lastHarmonicReportKey)
   {
      g_lastHarmonicReportState = "Duplicate local snapshot";
      return;
   }

   string description = StringFormat("SL=%s TP1=%s TP2=%s TP3=%s PA=%s",
                                     (slValue != EMPTY_VALUE && slValue > 0) ? DoubleToString(slValue, Digits) : "n/a",
                                     (tp1Value != EMPTY_VALUE && tp1Value > 0) ? DoubleToString(tp1Value, Digits) : "n/a",
                                     (tp2Value != EMPTY_VALUE && tp2Value > 0) ? DoubleToString(tp2Value, Digits) : "n/a",
                                     (tp3Value != EMPTY_VALUE && tp3Value > 0) ? DoubleToString(tp3Value, Digits) : "n/a",
                                     hasPriceAction ? "true" : "false");

   string json = "{";
   json += "\"id\":\"" + JsonSafeText(reportKey) + "\",";
   json += "\"type\":\"harmonic\",";
   json += "\"indicator\":\"shepherd_harmonic\",";
   json += "\"direction\":\"" + JsonSafeText(direction) + "\",";
   json += "\"symbol\":\"" + JsonSafeText(symbol) + "\",";
   json += "\"timeframe\":\"" + JsonSafeText(timeframe) + "\",";
   json += "\"time\":\"" + JsonSafeText(TimeToStr(signalTime, TIME_DATE|TIME_MINUTES)) + "\",";
   json += "\"price\":" + DoubleToString(signalPrice, Digits) + ",";
   json += "\"strength\":\"local\",";
   json += "\"confidence\":0,";
   json += "\"description\":\"" + JsonSafeText(description) + "\"";
   json += "}";

   string response = HttpPost("/indicator_alert/store", json);
   g_lastHarmonicReportKey = reportKey;
   if(StringLen(response) > 0)
      g_lastHarmonicReportState = "Reported local snapshot";
   else
      g_lastHarmonicReportState = "Local report failed";
}

//+------------------------------------------------------------------+
void DrawServerHarmonicAlert(string symbol, string indicator, string direction, string timeframe,
                             double price, string strength, double confidence, string description,
                             datetime alertTime)
{
   if(StringLen(symbol) == 0) symbol = Symbol();
   if(StringLen(timeframe) == 0) timeframe = TimeframeToString(Period());
   if(price <= 0) price = Close[0];
   if(alertTime == 0) alertTime = TimeCurrent();

   if(direction == "bullish" || direction == "bearish")
      DrawHarmonicSignalArrow(alertTime, price, direction, "Server");

   g_lastHarmonicReportState = "Server alert";
   UpdateHarmonicPanel(symbol, timeframe, direction == "" ? "Server alert" : direction,
                       "server", false, EMPTY_VALUE, EMPTY_VALUE, EMPTY_VALUE, EMPTY_VALUE,
                       g_lastHarmonicReportState);
}

//+------------------------------------------------------------------+
void PollLocalHarmonicIndicator()
{
   if(!EnableHarmonicVisuals && !EnableHarmonicReport)
      return;

   int pollSeconds = HarmonicPollSeconds;
   if(pollSeconds < 1) pollSeconds = 1;

   datetime now = TimeCurrent();
   if(g_lastHarmonicPollTime != 0 && now - g_lastHarmonicPollTime < pollSeconds)
      return;
   g_lastHarmonicPollTime = now;

   int shift = HarmonicLookbackShift;
   if(shift < 0) shift = 0;

   ResetLastError();
   double buy_signal      = iCustom(NULL, 0, HarmonicIndicatorName, 0, shift);
   double sell_signal     = iCustom(NULL, 0, HarmonicIndicatorName, 1, shift);
   double bull_divergence = iCustom(NULL, 0, HarmonicIndicatorName, 2, shift);
   double bear_divergence = iCustom(NULL, 0, HarmonicIndicatorName, 3, shift);
   double sl_Value        = iCustom(NULL, 0, HarmonicIndicatorName, 4, shift);
   double tp1_Value       = iCustom(NULL, 0, HarmonicIndicatorName, 5, shift);
   double tp2_Value       = iCustom(NULL, 0, HarmonicIndicatorName, 6, shift);
   double tp3_Value       = iCustom(NULL, 0, HarmonicIndicatorName, 7, shift);
   double price_Action    = iCustom(NULL, 0, HarmonicIndicatorName, 8, shift);
   int indicatorError = GetLastError();

   bool hasBuy = HasIndicatorValue(buy_signal);
   bool hasSell = HasIndicatorValue(sell_signal);
   bool hasBullDiv = HasIndicatorValue(bull_divergence);
   bool hasBearDiv = HasIndicatorValue(bear_divergence);
   bool hasPriceAction = HasIndicatorValue(price_Action);

   string direction = "";
   if(hasBuy || hasBullDiv)
      direction = "bullish";
   else if(hasSell || hasBearDiv)
      direction = "bearish";

   double signalPrice = Close[shift];
   if(hasBuy) signalPrice = buy_signal;
   else if(hasSell) signalPrice = sell_signal;
   else if(hasBullDiv) signalPrice = bull_divergence;
   else if(hasBearDiv) signalPrice = bear_divergence;

   datetime signalTime = iTime(Symbol(), Period(), shift);
   if(signalTime == 0) signalTime = TimeCurrent();

   string timeframe = TimeframeToString(Period());
   string divergenceState = "none";
   if(hasBullDiv) divergenceState = "bullish";
   else if(hasBearDiv) divergenceState = "bearish";

   bool hasDirectionalSignal = (hasBuy || hasSell || hasBullDiv || hasBearDiv);
   bool hasAnySignal = (hasDirectionalSignal || hasPriceAction);

   if(!hasAnySignal)
   {
      g_lastHarmonicReportState = EnableHarmonicReport ? g_lastHarmonicReportState : "Not reporting";
      if(EnableHarmonicVisuals)
      {
         DrawHarmonicLevelLine("SL", sl_Value, clrOrangeRed);
         DrawHarmonicLevelLine("TP1", tp1_Value, clrLimeGreen);
         DrawHarmonicLevelLine("TP2", tp2_Value, clrDeepSkyBlue);
         DrawHarmonicLevelLine("TP3", tp3_Value, clrDodgerBlue);
         UpdateHarmonicPanel(Symbol(), timeframe, "No local signal", "none", false,
                             sl_Value, tp1_Value, tp2_Value, tp3_Value, g_lastHarmonicReportState);
      }

      if(now - g_lastHarmonicMissingLogTime >= 300)
      {
         if(indicatorError != 0)
            Print("⚠️ Harmonic indicator read failed: ", HarmonicIndicatorName, " error=", indicatorError);
         else
            Print("ℹ️ Harmonic indicator has no local signal");
         g_lastHarmonicMissingLogTime = now;
      }
      return;
   }

   if(EnableHarmonicVisuals)
   {
      if(direction == "bullish" || direction == "bearish")
         DrawHarmonicSignalArrow(signalTime, signalPrice, direction, "Local");

      if(hasBullDiv)
         DrawHarmonicDivergenceMarker(signalTime, bull_divergence, "bullish");
      if(hasBearDiv)
         DrawHarmonicDivergenceMarker(signalTime, bear_divergence, "bearish");

      DrawHarmonicLevelLine("SL", sl_Value, clrOrangeRed);
      DrawHarmonicLevelLine("TP1", tp1_Value, clrLimeGreen);
      DrawHarmonicLevelLine("TP2", tp2_Value, clrDeepSkyBlue);
      DrawHarmonicLevelLine("TP3", tp3_Value, clrDodgerBlue);
   }

   if(hasDirectionalSignal)
      ReportHarmonicSnapshot(Symbol(), timeframe, direction, signalTime, signalPrice,
                             sl_Value, tp1_Value, tp2_Value, tp3_Value, hasPriceAction);

   string signalState = direction;
   if(StringLen(signalState) == 0)
      signalState = hasPriceAction ? "Price action only" : "No local signal";

   if(EnableHarmonicVisuals)
      UpdateHarmonicPanel(Symbol(), timeframe, signalState, divergenceState, hasPriceAction,
                          sl_Value, tp1_Value, tp2_Value, tp3_Value, g_lastHarmonicReportState);
}

//+------------------------------------------------------------------+
//| 轮询并显示指标背离信号                                             |
//+------------------------------------------------------------------+
void PollIndicatorAlerts()
{
   string json = StringFormat("{\"account_id\":\"%s\"}", AccountID);
   string response = HttpPost("/indicator_alert/poll", json);

   if(StringLen(response) == 0) return;

   int count = GetJsonInt(response, "count");
   if(count == 0) return;

   Print("📊 收到 ", count, " 条指标警报");
   string alerts_str = GetJsonArraySafe(response, "alerts");

   for(int i = 0; i < count; i++)
   {
      string alert = GetArrayElement(alerts_str, i);
      if(StringLen(alert) == 0) continue;

      string alertType = GetJsonStringSafe(alert, "type");
      string indicator = GetJsonStringSafe(alert, "indicator");
      string direction = GetJsonStringSafe(alert, "direction");
      string symbol = GetJsonStringSafe(alert, "symbol");
      string timeframe = GetJsonStringSafe(alert, "timeframe");
      double price = GetJsonDouble(alert, "price");
      string strength = GetJsonStringSafe(alert, "strength");
      double confidence = GetJsonDouble(alert, "confidence");
      string description = GetJsonStringSafe(alert, "description");

      if(StringLen(symbol) == 0 && g_symbolCount > 0)
         symbol = g_symbols[0];

      string timeStr = GetJsonStringSafe(alert, "time");
      datetime alertTime = ParseAlertTime(timeStr);
      if(alertTime == 0) alertTime = TimeCurrent();

      if(alertType == "harmonic")
      {
         DrawServerHarmonicAlert(symbol, indicator, direction, timeframe, price, strength, confidence, description, alertTime);
      }
      else
      {
         DrawDivergenceArrow(symbol, indicator, direction, price, strength, confidence, alertTime);
      }

      Print("📈 指标警报: ", indicator, " ", direction, " @", price, " [", strength, "]");
   }
}

//+------------------------------------------------------------------+
//| 绘制背离箭头                                                       |
//+------------------------------------------------------------------+
void DrawDivergenceArrow(string symbol, string indicator, string direction, 
                          double price, string strength, double confidence, datetime time)
{
   // 生成唯一对象名称
   string objName = g_indicatorObjectPrefix + indicator + "_" + direction + "_" + IntegerToString(TimeCurrent());
   
   // 删除同名旧对象（防止重复）
   if(ObjectFind(0, objName) >= 0)
      ObjectDelete(0, objName);
   
   // 选择颜色和箭头类型
   color arrowColor;
   int arrowCode;
   
   if(direction == "bullish")
   {
      arrowColor = clrLime;           // 绿色 - 看涨
      arrowCode = 233;                // 上箭头
   }
   else // bearish
   {
      arrowColor = clrRed;            // 红色 - 看跌
      arrowCode = 234;                // 下箭头
   }
   
   // 根据强度调整颜色深浅
   if(strength == "moderate")
   {
      if(direction == "bullish") arrowColor = clrGreen;
      else arrowColor = clrDarkOrange;
   }
   else if(strength == "weak")
   {
      if(direction == "bullish") arrowColor = clrLightGreen;
      else arrowColor = clrLightSalmon;
   }
   
   // 创建箭头对象
   if(!ObjectCreate(0, objName, OBJ_ARROW, 0, time, price))
   {
      Print("❌ 无法创建箭头对象: ", objName);
      return;
   }
   
   // 设置箭头属性
   ObjectSetInteger(0, objName, OBJPROP_ARROWCODE, arrowCode);
   ObjectSetInteger(0, objName, OBJPROP_COLOR, arrowColor);
   ObjectSetInteger(0, objName, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, objName, OBJPROP_ANCHOR, ANCHOR_BOTTOM);
   ObjectSetInteger(0, objName, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, objName, OBJPROP_SELECTED, false);
   
   // 添加文字标签（在图表角落显示详细信息）
   string labelName = g_indicatorObjectPrefix + "Label_" + IntegerToString(TimeCurrent());
   if(ObjectFind(0, labelName) < 0)
   {
      ObjectCreate(0, labelName, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, labelName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, labelName, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, labelName, OBJPROP_YDISTANCE, 20 + g_indicatorObjectCount * 15);
      ObjectSetInteger(0, labelName, OBJPROP_COLOR, arrowColor);
      ObjectSetInteger(0, labelName, OBJPROP_FONTSIZE, 8);
   }
   
   string labelText = indicator + " " + direction + " [" + strength + "]";
   ObjectSetString(0, labelName, OBJPROP_TEXT, labelText);
   
   // 更新对象计数
   g_indicatorObjectCount++;
   if(g_indicatorObjectCount > 20)
   {
      // 清理最旧的对象
      CleanOldIndicatorObjects();
      g_indicatorObjectCount = 0;
   }
}

//+------------------------------------------------------------------+
//| 清理旧的指标对象                                                   |
//+------------------------------------------------------------------+
void CleanOldIndicatorObjects()
{
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, g_indicatorObjectPrefix) == 0)
      {
         ObjectDelete(0, name);
      }
   }
   Print("🧹 清理指标对象，删除了 ", total, " 个对象");
}

//+------------------------------------------------------------------+
//| 更新图表角落文字摘要                                               |
//+------------------------------------------------------------------+
void UpdateChartComment(string summary)
{
   Comment("GoldBolt Technical View\n" +
           "=======================\n" +
           summary);
}

//+------------------------------------------------------------------+
