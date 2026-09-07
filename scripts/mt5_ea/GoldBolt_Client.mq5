//+------------------------------------------------------------------+
//| GoldBolt_Client.mq5                                               |
//| 纯执行器 - 所有策略逻辑在服务端                                     |
//| EA 只负责：风控参数 + 执行指令 + 推送数据                           |
//| v2.5: 服务器重启自动恢复连接                                        |
//+------------------------------------------------------------------+
#property copyright "Gold Bolt"
#property version   "2.9.5"
#property strict

// 引入交易库
#include <Trade/Trade.mqh>

// ============ 版本信息 ============
#define EA_VERSION  "2.9.5"
#define EA_BUILD    15

CTrade trade;

//+------------------------------------------------------------------+
//| 服务器连接配置                                                      |
//+------------------------------------------------------------------+
input string   ServerURL       = "http://127.0.0.1:8880";  // 服务端地址
input string   AccountID       = "account_A";              // 账户 ID
input string   ApiToken        = "";                       // API Token

//+------------------------------------------------------------------+
//| 风控参数配置（用户自行调整）                                        |
//+------------------------------------------------------------------+
input double   MaxRiskPercent  = 2.0;      // 单笔最大风险 %
input int      MaxPositions    = 5;        // 最大持仓数
input double   MaxDailyLoss    = 5.0;      // 日最大亏损 %
input double   MaxSpread       = 5.0;      // 最大点差（points）
input int      MaxSameDir      = 3;        // 同方向最大持仓数
input double   MaxFloatLoss    = 3.0;      // 最大浮亏 %
input bool     UseFixedLots    = true;     // 优先固定手数
input double   FixedLots       = 0.10;     // 固定手数（UseFixedLots=true 时生效；SymbolLotsMap 未命中时回退）
input string   SymbolLotsMap   = "";       // 按品种手数：XAUUSD:0.10,US100:0.05（空=全部用 FixedLots）
input string   StrategyLotsMap = "";       // 按策略手数：ai_signal:0.01,pullback:0.10（空=不覆盖）

//+------------------------------------------------------------------+
//| 策略启用配置（EA 端控制）                                           |
//+------------------------------------------------------------------+
input group "===== 策略开关与Magic编号 ====="
input bool     EnablePullback      = true;     // 📈 趋势回调策略
input int      PullbackMagic       = 20250231; //趋势回调 Magic

input bool     EnableBreakout      = true;     // 🔥 突破回踩策略
input int      BreakoutMagic       = 20250232; // 突破回踩 Magic

input bool     EnableDivergence    = true;     // 📊 RSI 背离策略
input int      DivergenceMagic     = 20250233; // RSI 背离 Magic

input bool     EnablePyramid       = true;     // 🏗️ 突破加仓策略
input int      PyramidMagic        = 20250234; // 突破加仓 Magic

input bool     EnableCounter       = false;    // 🔄 反向回调加仓
input int      CounterMagic        = 20250235; // 反向回调 Magic

input bool     EnableRange         = false;    // 📊 震荡市区间策略
input int      RangeMagic          = 20250236; // 震荡市区间 Magic

input bool     EnableMomentumScalp       = false;    // ⚡ 动量剥头皮策略
input int      MomentumScalpMagic        = 20250237; // 动量剥头皮 Magic
input bool     MomentumScalpUseFixedLots = true;     // 动量剥头皮使用固定手数
input double   MomentumScalpFixedLots    = 0.05;     // 动量剥头皮固定手数
input double   MomentumScalpRiskPercent  = 0.5;      // 动量剥头皮单笔风险 %

input bool     EnableAISignal      = true;     // 🤖 AI 信号挂单策略
input int      AISignalMagic       = 20250238; // AI 信号 Magic

input bool     EnableScaleIn       = true;     // ➕ 浮亏加仓策略
input int      ScaleInMagic        = 20250239; // 浮亏加仓 Magic

//+------------------------------------------------------------------+
//| 原油对冲套利配置                                                   |
//+------------------------------------------------------------------+
input group "===== 原油对冲套利 ====="
input bool     EnableSpread        = false;    // 🛢️ 启用原油对冲套利
input int      SpreadMagicNumber   = 20250224; // 原油策略魔术号
input string   SpreadSymbol1       = "UKOIL";  // 腿 1: Brent (布伦特)
input string   SpreadSymbol2       = "USOIL";  // 腿 2: WTI (美国)
input double   SpreadLots          = 0.05;     // 每腿交易手数

//+------------------------------------------------------------------+
//| 通信参数配置                                                       |
//+------------------------------------------------------------------+
input int      PollInterval        = 5;        // 轮询间隔（秒）
input int      BarInterval         = 60;       // K 线发送间隔（秒）
input int      BarCount            = 50;       // K 线数量
input string   Symbol_             = "XAUUSD"; // 主交易品种
input string   Symbols             = "XAUUSD"; // 交易品种（逗号分隔多个）
input string   AISymbols           = "";       // AI 分析品种（逗号分隔，留空=与交易品种相同）
input string   SymbolSuffix        = "";       // 经纪商品种后缀（如 .m, m#, _m），留空=无后缀
input int      Slippage            = 3;        // 滑点（点数）

input group "===== 可视化桥接 ====="
input bool     EnableVisualBridge       = true;
input int      VisualBridgePollSeconds  = 5;
input int      VisualBridgeTimeoutMs    = 1500;
input string   VisualBridgeTimeframes   = "M1,M5,M15,M30,H1,H4,D1";
input bool     VisualBridgeCommonFiles  = true;

// ============ 全局变量 ============
datetime lastPollTime      = 0;
datetime lastBarTime       = 0;
datetime lastHistoryTime   = 0;       // 上次上报已平仓成交的时间
double   dailyStartEquity  = 0;
int      httpTimeout       = 2000;
bool     spreadSymbolsReady = false;  // 原油品种是否可用
string   g_lotMapSymbols[];           // SymbolLotsMap 解析后的品种 key
double   g_lotMapValues[];            // SymbolLotsMap 解析后的手数
int      g_lotMapCount = 0;
string   g_strategyLotMapNames[];     // StrategyLotsMap 解析后的策略 key
double   g_strategyLotMapValues[];    // StrategyLotsMap 解析后的手数
int      g_strategyLotMapCount = 0;
string   g_symbols[];                 // 解析后的品种列表（基础名，不含后缀）
int      g_symbolCount = 0;
string   g_ai_symbols[];              // AI 分析品种列表
int      g_ai_symbol_count = 0;

// ========== 可视化桥接 ==========
datetime g_lastVisualBridgePollTime = 0;
int      g_visualBridgeSymbolIndex = 0;
int      g_visualBridgeTimeframeIndex = 0;

// ========== 连接状态跟踪（v2.8 新增） ==========
bool     gbConnected      = false;        // 当前连接状态
datetime lastSuccessTime  = 0;            // 最后成功通信时间
int      failCount        = 0;            // 连续失败次数
datetime lastReconnectTry = 0;            // 上次重连尝试时间
datetime lastRegisterTry  = 0;            // 上次注册尝试时间（每5秒重试）
bool     gbRegistered     = false;        // 注册是否成功

// ========== 初始化分批发送（避免 OnInit 同步阻塞图表线程）==========
// OnInit 只做 RegisterAccount；心跳 / 持仓 / K线 拆分到 OnTick 首批 tick 内逐项发送
// 0=未开始 1=待发心跳 2=待发持仓 3=待发K线 4=初始数据已全部发送
int      g_initBatchStep = 0;

//+------------------------------------------------------------------+
//| 根据策略名称获取对应的 MagicNumber                                  |
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

      StringTrimLeft(token);
      StringTrimRight(token);
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
   StringTrimLeft(symbolList);
   StringTrimRight(symbolList);

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

      StringTrimLeft(token);
      StringTrimRight(token);
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

//+------------------------------------------------------------------+
bool SelectPositionByIndex(int index)
{
   if(index < 0 || index >= PositionsTotal())
      return false;

   ulong ticket = PositionGetTicket(index);
   if(ticket == 0)
      return false;

   return PositionSelectByTicket(ticket);
}

//+------------------------------------------------------------------+
bool IsOurMagic(long magic)
{
   if(magic == PullbackMagic) return true;
   if(magic == BreakoutMagic) return true;
   if(magic == DivergenceMagic) return true;
   if(magic == PyramidMagic) return true;
   if(magic == CounterMagic) return true;
   if(magic == RangeMagic) return true;
   if(magic == MomentumScalpMagic) return true;
   if(magic == SpreadMagicNumber) return true;
   if(magic == AISignalMagic) return true;
   if(magic == ScaleInMagic) return true;
   return false;
}

//+------------------------------------------------------------------+
bool IsPrimarySymbol(string symbol)
{
   return FindSymbolInArray(symbol);
}

//+------------------------------------------------------------------+
bool IsSpreadSymbol(string symbol)
{
   if(StringLen(symbol) == 0)
      return false;

   return (symbol == SpreadSymbol1 || symbol == SpreadSymbol2);
}

//+------------------------------------------------------------------+
bool IsAllowedSymbol(string symbol)
{
   return (IsPrimarySymbol(symbol) || IsSpreadSymbol(symbol));
}

//+------------------------------------------------------------------+
bool IsTrackedSymbol(string symbol)
{
   return IsAllowedSymbol(symbol);
}

// 解析 SymbolLotsMap: "XAUUSD:0.10,US100:0.05"
void ParseSymbolLotsMap(string map)
{
   g_lotMapCount = 0;
   ArrayResize(g_lotMapSymbols, 0);
   ArrayResize(g_lotMapValues, 0);

   string remaining = map;
   StringTrimLeft(remaining);
   StringTrimRight(remaining);
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

      StringTrimLeft(token);
      StringTrimRight(token);
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
      StringTrimLeft(sym);
      StringTrimRight(sym);
      StringTrimLeft(lotsStr);
      StringTrimRight(lotsStr);

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

// 按品种取固定手数：精确匹配优先，其次最长前缀匹配
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
   StringTrimLeft(strategy);
   StringTrimRight(strategy);
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
   StringTrimLeft(remaining);
   StringTrimRight(remaining);
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

      StringTrimLeft(token);
      StringTrimRight(token);
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
      StringTrimLeft(lotsStr);
      StringTrimRight(lotsStr);

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
double GetSymbolPoint(string symbol)
{
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(point <= 0)
      point = _Point;
   return point;
}

//+------------------------------------------------------------------+
double GetCurrentSpreadPoints(string symbol)
{
   double currentSpread = (double)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   if(currentSpread > 0)
      return currentSpread;

   double point = GetSymbolPoint(symbol);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   if(point <= 0 || bid <= 0 || ask <= 0)
      return -1.0;

   currentSpread = (ask - bid) / point;
   return currentSpread;
}

//+------------------------------------------------------------------+
int GetVolumeDigits(string symbol)
{
   double stepLots = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
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
double NormalizeVolume(string symbol, double lots)
{
   double minLots  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLots  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double stepLots = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

   if(stepLots <= 0) stepLots = 0.01;
   if(minLots <= 0) minLots = stepLots;
   if(maxLots <= 0) maxLots = lots;

   lots = MathMax(minLots, MathMin(maxLots, lots));
   lots = MathFloor(lots / stepLots) * stepLots;
   return NormalizeDouble(MathMax(minLots, lots), GetVolumeDigits(symbol));
}

//+------------------------------------------------------------------+
double NormalizeCloseVolume(string symbol, double lots)
{
   double minLots  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLots  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double stepLots = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

   if(stepLots <= 0) stepLots = 0.01;
   if(minLots <= 0) minLots = stepLots;
   if(maxLots <= 0) maxLots = lots;

   lots = MathMax(0.0, MathMin(maxLots, lots));
   double normalizedLots = MathFloor((lots + 0.0000001) / stepLots) * stepLots;
   if(normalizedLots + 0.0000001 < minLots)
      return 0.0;

   return NormalizeDouble(normalizedLots, GetVolumeDigits(symbol));
}

//+------------------------------------------------------------------+
void PrepareTrade(string symbol, long magic)
{
   trade.SetExpertMagicNumber((ulong)magic);
   trade.SetDeviationInPoints((ulong)Slippage);
   trade.SetTypeFillingBySymbol(symbol);
}

//+------------------------------------------------------------------+
bool IsTradeRetcodeSuccess()
{
   uint retcode = trade.ResultRetcode();
   return (retcode == TRADE_RETCODE_DONE ||
           retcode == TRADE_RETCODE_PLACED);
}

//+------------------------------------------------------------------+
bool TradeOperationSucceeded(bool requestSent)
{
   return (requestSent && IsTradeRetcodeSuccess());
}

//+------------------------------------------------------------------+
bool IsTradeRetcodePartialFill()
{
   return (trade.ResultRetcode() == TRADE_RETCODE_DONE_PARTIAL);
}

//+------------------------------------------------------------------+
bool TradeOperationPartiallyFilled(bool requestSent)
{
   return (requestSent && IsTradeRetcodePartialFill());
}

//+------------------------------------------------------------------+
int GetTradeErrorCode()
{
   int err = (int)trade.ResultRetcode();
   if(err == 0)
      err = GetLastError();
   return err;
}

//+------------------------------------------------------------------+
string FormatLongValue(long value)
{
   return StringFormat("%I64d", value);
}

//+------------------------------------------------------------------+
string FormatULongValue(ulong value)
{
   return StringFormat("%I64u", value);
}

//+------------------------------------------------------------------+
ulong FindLatestPositionTicket(string symbol, long magic, ENUM_POSITION_TYPE posType)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!SelectPositionByIndex(i))
         continue;

      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;

      if(PositionGetInteger(POSITION_MAGIC) != magic)
         continue;

      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != posType)
         continue;

      return (ulong)PositionGetInteger(POSITION_TICKET);
   }

   return 0;
}

//+------------------------------------------------------------------+
bool SelectedPositionMatches(string symbol, long magic, ENUM_POSITION_TYPE posType)
{
   if(PositionGetString(POSITION_SYMBOL) != symbol)
      return false;

   if(PositionGetInteger(POSITION_MAGIC) != magic)
      return false;

   if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != posType)
      return false;

   return true;
}

//+------------------------------------------------------------------+
ulong FindPositionTicketByIdentifier(long positionId, string symbol, long magic, ENUM_POSITION_TYPE posType)
{
   if(positionId <= 0)
      return 0;

   ulong matchedTicket = 0;
   int matchedCount = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!SelectPositionByIndex(i))
         continue;

      if(PositionGetInteger(POSITION_IDENTIFIER) != positionId)
         continue;

      if(!SelectedPositionMatches(symbol, magic, posType))
         continue;

      matchedTicket = (ulong)PositionGetInteger(POSITION_TICKET);
      matchedCount++;
      if(matchedCount > 1)
      {
         Print("⚠️ position identifier 对应多个实时持仓，拒绝继续: id=", FormatLongValue(positionId));
         return 0;
      }
   }

   if(matchedCount == 1)
      return matchedTicket;

   return 0;
}

//+------------------------------------------------------------------+
ulong FindUniquePositionTicket(string symbol, long magic, ENUM_POSITION_TYPE posType)
{
   ulong matchedTicket = 0;
   int matchedCount = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!SelectPositionByIndex(i))
         continue;

      if(!SelectedPositionMatches(symbol, magic, posType))
         continue;

      matchedTicket = (ulong)PositionGetInteger(POSITION_TICKET);
      matchedCount++;
      if(matchedCount > 1)
      {
         Print("⚠️ 存在多个同 symbol/magic/type 持仓，无法安全解析目标持仓: ", symbol,
               " | Magic=", magic, " | Type=", (int)posType);
         return 0;
      }
   }

   if(matchedCount == 1)
      return matchedTicket;

   return 0;
}

//+------------------------------------------------------------------+
ulong ResolvePositionTicket(ulong rawTicket, string symbol, long magic, ENUM_POSITION_TYPE posType)
{
   if(rawTicket != 0 && PositionSelectByTicket(rawTicket) && SelectedPositionMatches(symbol, magic, posType))
      return rawTicket;

   ulong dealTicket = (ulong)trade.ResultDeal();
   if(dealTicket != 0 && HistoryDealSelect(dealTicket))
   {
      long positionId = HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
      ulong ticketByIdentifier = FindPositionTicketByIdentifier(positionId, symbol, magic, posType);
      if(ticketByIdentifier != 0)
         return ticketByIdentifier;
   }

   return FindUniquePositionTicket(symbol, magic, posType);
}

//+------------------------------------------------------------------+
ulong ResolveLivePositionTicket(ulong rawTicket, string symbol, long magic, ENUM_POSITION_TYPE posType)
{
   ulong ticket = ResolvePositionTicket(rawTicket, symbol, magic, posType);
   if(ticket == 0)
      return 0;

   if(!PositionSelectByTicket(ticket))
      return 0;

   if(!SelectedPositionMatches(symbol, magic, posType))
      return 0;

   return ticket;
}

//+------------------------------------------------------------------+
string EnsureSignalProtectionAttached(ulong ticket, string type_str, double sl, double tp1)
{
   if(ticket == 0 || !PositionSelectByTicket(ticket))
   {
      Print("⚠️ 开仓后未能选中持仓 #", FormatULongValue(ticket), "，无法安全附加保护止损");
      return "position_resolve_incomplete";
   }

   string positionSymbol = PositionGetString(POSITION_SYMBOL);
   long positionMagic = PositionGetInteger(POSITION_MAGIC);
   double current_sl = PositionGetDouble(POSITION_SL);
   double current_tp = PositionGetDouble(POSITION_TP);

   if(current_sl == 0.0 || current_tp == 0.0)
   {
      double min_stop = (double)SymbolInfoInteger(positionSymbol, SYMBOL_TRADE_STOPS_LEVEL) * GetSymbolPoint(positionSymbol);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double final_sl = sl;
      double final_tp = tp1;

      if(min_stop > 0)
      {
         if(MathAbs(openPrice - sl) < min_stop)
         {
            if(type_str == "BUY") final_sl = openPrice - min_stop;
            else final_sl = openPrice + min_stop;
         }

         if(MathAbs(tp1 - openPrice) < min_stop)
         {
            if(type_str == "BUY") final_tp = openPrice + min_stop;
            else final_tp = openPrice - min_stop;
         }
      }

      if(final_sl != current_sl || final_tp != current_tp)
      {
         PrepareTrade(positionSymbol, positionMagic);
         if(TradeOperationSucceeded(trade.PositionModify(ticket, final_sl, final_tp)))
            Print("📝 开仓后设置 TP/SL: SL=", final_sl, " TP=", final_tp);
         else
         {
            int mod_err = GetTradeErrorCode();
            Print("⚠️ 开仓成功但保护止损附加失败: #", FormatULongValue(ticket), " Error#", mod_err);
            return "protection_attach_failed";
         }
      }
   }

   if(!PositionSelectByTicket(ticket))
   {
      Print("⚠️ 开仓后无法重新选中持仓 #", FormatULongValue(ticket), "，保护状态未确认");
      return "position_resolve_incomplete";
   }

   if(PositionGetDouble(POSITION_SL) == 0.0 || PositionGetDouble(POSITION_TP) == 0.0)
   {
      Print("⚠️ 开仓后保护止损未完整附加: #", FormatULongValue(ticket));
      return "protection_attach_incomplete";
   }

   return "";
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

   if(g_symbolCount == 0)
   {
      Print("❌ 未配置任何交易品种（Symbols 为空）");
      return INIT_FAILED;
   }

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
         " 动量剥头皮=", MomentumScalpMagic, " AI信号=", AISignalMagic,
         " 浮亏加仓=", ScaleInMagic);
   Print("风控：",
         (UseFixedLots ? ("固定手数默认=" + DoubleToString(FixedLots, 2)) : ("风险=" + DoubleToString(MaxRiskPercent, 1) + "%")),
         " | 持仓上限", MaxPositions,
         " | 日亏损", MaxDailyLoss, "% | 浮亏", MaxFloatLoss, "%");
   Print("品种手数映射：", FormatSymbolLotsMap(),
         " | 主品种 lots=", DoubleToString(GetFixedLotsForSymbol(g_symbols[0], FixedLots), 2));
   Print("策略手数映射：", FormatStrategyLotsMap());
   Print("动量剥头皮：",
         (EnableMomentumScalp ? "启用" : "禁用"),
         " | ",
         (MomentumScalpUseFixedLots ? ("固定手数=" + DoubleToString(MomentumScalpFixedLots, 2)) : ("风险=" + DoubleToString(MomentumScalpRiskPercent, 1) + "%")));

   // 图表品种检查：允许挂载任意已配置品种的图表
   if(!FindSymbolInArray(_Symbol))
   {
      Print("⚠️ 图表品种 ", _Symbol, " 未在配置列表中 | 已配置: ", Symbols);
      Print("   EA 仍可运行，但建议挂载已配置品种的图表以获取最佳报价");
   }

   PrepareTrade(GetBrokerSymbol(g_symbols[0]), PullbackMagic);

   long marginMode = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(marginMode != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("❌ 当前 MT5 账户不是 Hedging 模式，无法等价复刻 MQ4 多策略/多持仓逻辑");
      Print("   当前模式=", (int)marginMode, " | 需要 ACCOUNT_MARGIN_MODE_RETAIL_HEDGING");
      return INIT_FAILED;
   }

   if(EnableSpread)
   {
      Print("🛢️ 原油对冲套利：启用");
      Print("   Magic: ", SpreadMagicNumber);
      Print("   腿 1: ", SpreadSymbol1, " (BUY)");
      Print("   腿 2: ", SpreadSymbol2, " (SELL)");
      Print("   手数：", SpreadLots);

      if(IsSymbolAvailable(SpreadSymbol1) && IsSymbolAvailable(SpreadSymbol2))
      {
         spreadSymbolsReady = true;
         Print("   ✅ 品种可用");
      }
      else
      {
         spreadSymbolsReady = false;
         Print("   ⚠️ 品种不可用，请检查经纪商是否支持");
      }
   }
   else
   {
      Print("🛢️ 原油对冲套利：禁用");
   }

   Print("📊 扫描已有持仓...");
   int pullbackCount = 0, breakoutCount = 0, divergenceCount = 0;
   int pyramidCount = 0, counterCount = 0, rangeCount = 0, momentumScalpCount = 0, spreadCount = 0;
   int aiSignalCount = 0, scaleInCount = 0;

   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!SelectPositionByIndex(i))
         continue;

      string positionSymbol = PositionGetString(POSITION_SYMBOL);
      if(!IsAllowedSymbol(positionSymbol))
         continue;

      long magic = PositionGetInteger(POSITION_MAGIC);
      string type = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL");
      string info = positionSymbol + " " + type + " " +
                    DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) +
                    " 手 | Ticket=" + FormatLongValue(PositionGetInteger(POSITION_TICKET));

      if(magic == PullbackMagic)      { pullbackCount++;  Print("   📈 趋势回调: ", info); }
      else if(magic == BreakoutMagic) { breakoutCount++;  Print("   🔥 突破回踩: ", info); }
      else if(magic == DivergenceMagic){ divergenceCount++; Print("   📊 RSI背离: ", info); }
      else if(magic == PyramidMagic)  { pyramidCount++;   Print("   🏗️ 突破加仓: ", info); }
      else if(magic == CounterMagic)  { counterCount++;   Print("   🔄 反向回调: ", info); }
      else if(magic == RangeMagic)    { rangeCount++;     Print("   📊 震荡区间: ", info); }
      else if(magic == MomentumScalpMagic){ momentumScalpCount++; Print("   ⚡ 动量剥头皮: ", info); }
      else if(magic == SpreadMagicNumber){ spreadCount++; Print("   🛢️ 原油对冲: ", info); }
      else if(magic == AISignalMagic){ aiSignalCount++; Print("   🤖 AI信号: ", info); }
      else if(magic == ScaleInMagic){ scaleInCount++; Print("   ➕ 浮亏加仓: ", info); }
   }

   Print("   趋势回调: ", pullbackCount, " 单 | 突破回踩: ", breakoutCount, " 单 | RSI背离: ", divergenceCount, " 单");
   Print("   突破加仓: ", pyramidCount, " 单 | 反向回调: ", counterCount, " 单 | 震荡区间: ", rangeCount, " 单 | 动量剥头皮: ", momentumScalpCount, " 单");
   Print("   原油对冲: ", spreadCount, " 单 | AI信号: ", aiSignalCount, " 单 | 浮亏加仓: ", scaleInCount, " 单");
   Print("=============================================");

   dailyStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);

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
      g_initBatchStep = 1;
      Print("✅ 注册成功，初始数据(心跳/持仓/K线)将由 OnTick 分批发送...");
   }

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime now = TimeCurrent();

   static bool firstTick = false;
   if(!firstTick)
   {
      Print("📡 首次 Tick 收到");
      firstTick = true;
   }

   // ========== 初始化分批发送：每个 tick 推进一步 ==========
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
         lastBarTime = now;
         Print("✅ 初始数据发送完成");
      }
      return;
   }

   SendTick();

   if(!gbRegistered && now - lastRegisterTry >= 5)
   {
      lastRegisterTry = now;
      Print("🔄 尝试注册 GB Server...");
      if(RegisterAccount())
      {
         Print("✅ 注册成功，初始数据(心跳/持仓/K线)将由 OnTick 分批发送...");
         g_initBatchStep = 1;
      }
   }

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

   static int lastDay = -1;
   MqlDateTime tm;
   TimeToStruct(now, tm);
   int today = tm.day;
   if(today != lastDay)
   {
      dailyStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      lastDay = today;
      Print("📅 日切重置 | 起始权益：", dailyStartEquity);
   }
}

//+------------------------------------------------------------------+
//| 注册账户信息（含 broker 信息，服务端用于识别账户类型）               |
//+------------------------------------------------------------------+
bool RegisterAccount()
{
   string broker = AccountInfoString(ACCOUNT_COMPANY);
   string server = AccountInfoString(ACCOUNT_SERVER);
   string name = AccountInfoString(ACCOUNT_NAME);
   string type = "standard";
   if(StringFind(broker, "ECN") >= 0 || StringFind(server, "ECN") >= 0)
      type = "ecn";
   else if(StringFind(broker, "Pro") >= 0 || StringFind(server, "Pro") >= 0)
      type = "pro";

   int leverage = (int)AccountInfoInteger(ACCOUNT_LEVERAGE);
   string currency = AccountInfoString(ACCOUNT_CURRENCY);
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
      "\"spread_enabled\":%s,"
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
      MaxDailyLoss, (EnableSpread ? "true" : "false"), aiSymbolsJson
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

   Print("❌ 账户注册失败");
   return false;
}

//+------------------------------------------------------------------+
// 发送心跳（附带账户基础信息）
//+------------------------------------------------------------------+
void SendHeartbeat()
{
   string serverTime = TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES);
   bool isTradeAllowed = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0);
   bool marketOpen = ((ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(GetBrokerSymbol(g_symbols[0]), SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_DISABLED);
   string aiSymbolsJson = BuildAISymbolsJson();

   int pullbackPos = 0, breakoutPos = 0, divergencePos = 0;
   int pyramidPos = 0, counterPos = 0, rangePos = 0, momentumScalpPos = 0, scaleInPos = 0;

   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!SelectPositionByIndex(i))
         continue;

      if(!IsAllowedSymbol(PositionGetString(POSITION_SYMBOL)))
         continue;

      long m = PositionGetInteger(POSITION_MAGIC);
      if(m == PullbackMagic) pullbackPos++;
      else if(m == BreakoutMagic) breakoutPos++;
      else if(m == DivergenceMagic) divergencePos++;
      else if(m == PyramidMagic) pyramidPos++;
      else if(m == CounterMagic) counterPos++;
      else if(m == RangeMagic) rangePos++;
      else if(m == MomentumScalpMagic) momentumScalpPos++;
      else if(m == ScaleInMagic) scaleInPos++;
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
      AccountID, g_symbols[0], PullbackMagic,
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      AccountInfoDouble(ACCOUNT_MARGIN),
      AccountInfoDouble(ACCOUNT_MARGIN_FREE),
      AccountInfoString(ACCOUNT_CURRENCY),
      serverTime,
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
   if(TimeCurrent() - lastSend < 1)
      return;

   lastSend = TimeCurrent();

   string symbols_json = "";

   if(EnableSpread && spreadSymbolsReady)
   {
       double leg1_bid = SymbolInfoDouble(SpreadSymbol1, SYMBOL_BID);
       double leg2_bid = SymbolInfoDouble(SpreadSymbol2, SYMBOL_BID);
       double leg1_ask = SymbolInfoDouble(SpreadSymbol1, SYMBOL_ASK);
       double leg2_ask = SymbolInfoDouble(SpreadSymbol2, SYMBOL_ASK);

       if(leg1_ask <= 0)
          leg1_ask = leg1_bid + GetSymbolPoint(SpreadSymbol1) * 10.0;
       if(leg2_ask <= 0)
          leg2_ask = leg2_bid + GetSymbolPoint(SpreadSymbol2) * 10.0;

       if(leg1_bid > 0 && leg2_bid > 0)
       {
          double spread_val = leg1_bid - leg2_bid;
          symbols_json = StringFormat(
             ",\"symbols\":{"
             "\"%s\":{\"price\":%.2f,\"bid\":%.2f,\"ask\":%.2f},"
             "\"%s\":{\"price\":%.2f,\"bid\":%.2f,\"ask\":%.2f},"
             "\"SPREAD\":%.2f"
             "}",
             SpreadSymbol1, leg1_bid, leg1_bid, leg1_ask,
             SpreadSymbol2, leg2_bid, leg2_bid, leg2_ask,
             spread_val
          );
          Print("🛢️ 原油价格：", SpreadSymbol1, "=", leg1_bid, " | ", SpreadSymbol2, "=", leg2_bid,
                " | 价差=", DoubleToString(spread_val, 2));
       }
    }

   // 遍历所有交易品种，每个品种单独 POST /tick
   for(int s = 0; s < g_symbolCount; s++)
   {
      string baseSymbol = g_symbols[s];
      string brokerSym = GetBrokerSymbol(baseSymbol);
      double bid = SymbolInfoDouble(brokerSym, SYMBOL_BID);
      double ask = SymbolInfoDouble(brokerSym, SYMBOL_ASK);
      double spread = GetCurrentSpreadPoints(brokerSym);
      if(spread < 0)
         spread = 0.0;

      // 价差 legs 只附加到第一个品种的 tick payload 末尾（非空时才带逗号）
      string syms = "";
      if(s == 0)
         syms = symbols_json;

      string json = StringFormat(
         "{"
         "\"account_id\":\"%s\","
         "\"magic\":%d,"
         "\"symbol\":\"%s\","
         "\"bid\":%.5f,"
         "\"ask\":%.5f,"
         "\"spread\":%.3f,"
         "\"max_spread\":%.3f,"
         "\"time\":\"%s\"%s"
         "}",
         AccountID, PullbackMagic, baseSymbol, bid, ask, spread, MaxSpread,
         TimeToString(TimeCurrent(), TIME_SECONDS), syms
      );

      HttpPost("/tick", json);
   }
}

//+------------------------------------------------------------------+
// 发送所有 K 线数据
//+------------------------------------------------------------------+
void SendAllBars()
{
   string tf_names[] = {"M1","M5","M15","M30","H1","H4","D1"};
   ENUM_TIMEFRAMES tf_periods[] = {PERIOD_M1,PERIOD_M5,PERIOD_M15,PERIOD_M30,PERIOD_H1,PERIOD_H4,PERIOD_D1};

   for(int s = 0; s < g_symbolCount; s++)
   {
      for(int t = 0; t < 7; t++)
      {
         SendBars(g_symbols[s], tf_names[t], tf_periods[t]);
      }
   }
}

//+------------------------------------------------------------------+
void SendBars(string baseSymbol, string tf_str, ENUM_TIMEFRAMES tf_period)
{
   string brokerSym = GetBrokerSymbol(baseSymbol);
   string bars = "";
   for(int i = BarCount - 1; i >= 0; i--)
   {
      datetime t = iTime(brokerSym, tf_period, i);
      if(t == 0)
         continue;

      double o = iOpen(brokerSym, tf_period, i);
      double h = iHigh(brokerSym, tf_period, i);
      double l = iLow(brokerSym, tf_period, i);
      double c = iClose(brokerSym, tf_period, i);
      long   v = iVolume(brokerSym, tf_period, i);

      if(bars != "") bars += ",";
      bars += StringFormat(
         "{\"time\":%d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"volume\":%d}",
         (int)t, o, h, l, c, (int)v
      );
   }

   string json = "{\"account_id\":\"" + AccountID +
                 "\",\"symbol\":\"" + baseSymbol +
                 "\",\"magic\":" + IntegerToString(PullbackMagic) +
                 ",\"timeframe\":\"" + tf_str +
                 "\",\"bars\":[" + bars + "]}";

   HttpPost("/bars", json);
}

//+------------------------------------------------------------------+
// 订单类型 → 服务端可读字符串（区分市价仓/挂单）
//+------------------------------------------------------------------+
string OrderTypeToString(int ot)
{
   if(ot == (int)ORDER_TYPE_BUY)       return "BUY";
   if(ot == (int)ORDER_TYPE_SELL)      return "SELL";
   if(ot == (int)ORDER_TYPE_BUY_LIMIT) return "BUY_LIMIT";
   if(ot == (int)ORDER_TYPE_BUY_STOP)  return "BUY_STOP";
   if(ot == (int)ORDER_TYPE_SELL_LIMIT) return "SELL_LIMIT";
   if(ot == (int)ORDER_TYPE_SELL_STOP) return "SELL_STOP";
   return "UNKNOWN";
}

//+------------------------------------------------------------------+
bool IsPendingOrderType(int ot)
{
   return (ot == (int)ORDER_TYPE_BUY_LIMIT || ot == (int)ORDER_TYPE_BUY_STOP ||
           ot == (int)ORDER_TYPE_SELL_LIMIT || ot == (int)ORDER_TYPE_SELL_STOP);
}

//+------------------------------------------------------------------+
// comment: GB_<strategy>_... → strategy 名（长名优先）
//+------------------------------------------------------------------+
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
      // 精确匹配，或后接 '_'（Sxx 段）
      if(StringLen(rest) == nlen) return n;
      string next = StringSubstr(rest, nlen, 1);
      if(next == "_" || next == "") return n;
   }
   return "";
}

//+------------------------------------------------------------------+
// 发送持仓信息（按品种分别发送，市价仓 + 挂单合并上报）
//+------------------------------------------------------------------+
void SendPositions()
{
   for(int s = 0; s < g_symbolCount; s++)
   {
      string positions = "";
      int count = 0;
      string baseSymbol = g_symbols[s];

      // 市价仓
      for(int i = 0; i < PositionsTotal(); i++)
      {
         if(!SelectPositionByIndex(i))
            continue;

         string symbol = PositionGetString(POSITION_SYMBOL);
         if(symbol != baseSymbol && symbol != GetBrokerSymbol(baseSymbol))
            continue;

         long magic = PositionGetInteger(POSITION_MAGIC);
         if(!IsOurMagic(magic))
            continue;

         string comment = PositionGetString(POSITION_COMMENT);
         string type = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL");

         if(positions != "") positions += ",";
         positions += "{\"ticket\":" + FormatLongValue(PositionGetInteger(POSITION_TICKET)) +
                      ",\"symbol\":\"" + JsonSafeText(symbol) +
                      "\",\"type\":\"" + type +
                      "\",\"order_class\":\"market\"" +
                      ",\"lots\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) +
                      ",\"open_price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5) +
                      ",\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL), 5) +
                      ",\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP), 5) +
                      ",\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) +
                      ",\"open_time\":" + FormatLongValue(PositionGetInteger(POSITION_TIME)) +
                      ",\"comment\":\"" + JsonSafeText(comment) +
                      "\",\"magic\":" + FormatLongValue(magic) +
                      ",\"strategy\":\"" + StrategyFromComment(comment) + "\"}";
         count++;
      }

      // 挂单
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         ulong ordTicket = OrderGetTicket(i);
         if(ordTicket == 0)
            continue;

         string orderSym = OrderGetString(ORDER_SYMBOL);
         if(orderSym != baseSymbol && orderSym != GetBrokerSymbol(baseSymbol))
            continue;

         long magic = OrderGetInteger(ORDER_MAGIC);
         if(!IsOurMagic(magic))
            continue;

         string type = OrderTypeToString((int)OrderGetInteger(ORDER_TYPE));
         string comment = OrderGetString(ORDER_COMMENT);

         if(positions != "") positions += ",";
         positions += "{\"ticket\":" + FormatLongValue(OrderGetInteger(ORDER_TICKET)) +
                      ",\"symbol\":\"" + JsonSafeText(orderSym) +
                      "\",\"type\":\"" + type +
                      "\",\"order_class\":\"pending\"" +
                      ",\"lots\":" + DoubleToString(OrderGetDouble(ORDER_VOLUME_CURRENT), 2) +
                      ",\"open_price\":" + DoubleToString(OrderGetDouble(ORDER_PRICE_OPEN), 5) +
                      ",\"sl\":" + DoubleToString(OrderGetDouble(ORDER_SL), 5) +
                      ",\"tp\":" + DoubleToString(OrderGetDouble(ORDER_TP), 5) +
                      ",\"profit\":0" +
                      ",\"open_time\":" + FormatLongValue(OrderGetInteger(ORDER_TIME_SETUP)) +
                      ",\"comment\":\"" + JsonSafeText(comment) +
                      "\",\"magic\":" + FormatLongValue(magic) +
                      ",\"strategy\":\"" + StrategyFromComment(comment) + "\"}";
         count++;
      }

      string json = StringFormat(
         "{\"account_id\":\"%s\",\"symbol\":\"%s\",\"magic\":%d,\"positions\":[%s]}",
         AccountID, baseSymbol, PullbackMagic, positions
      );

      HttpPost("/positions", json);
   }
}

// ============================================================
// 上报已平仓成交（绩效追踪）
// MT5 以 deal 为单位：DEAL_ENTRY_OUT 即一次平仓事件（部分平仓会有多条）。
// 开仓价通过 position_id 反查对应的 DEAL_ENTRY_IN。
// ============================================================
datetime g_lastReportedCloseTime = 0;

void SendTradeHistory()
{
   // 只拉取最近 7 天的历史，避免全量扫描
   datetime from = TimeCurrent() - 7 * 24 * 3600;
   if(g_lastReportedCloseTime > from) from = g_lastReportedCloseTime;
   if(!HistorySelect(from, TimeCurrent())) return;

   int total = HistoryDealsTotal();
   if(total <= 0) return;

   // 第一遍：收集待上报的平仓 deal（外层 HistorySelect 的选择集不能在遍历中被覆盖）
   ulong  outTickets[];
   long   outPositions[];
   int    outCount = 0;
   ArrayResize(outTickets, total);
   ArrayResize(outPositions, total);

   datetime maxCloseTime = g_lastReportedCloseTime;

   for(int i = total - 1; i >= 0 && outCount < 200; i--)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;

      ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) continue;

      ENUM_DEAL_TYPE dtype = (ENUM_DEAL_TYPE)HistoryDealGetInteger(dealTicket, DEAL_TYPE);
      if(dtype != DEAL_TYPE_BUY && dtype != DEAL_TYPE_SELL) continue;

      if(!IsOurMagic(HistoryDealGetInteger(dealTicket, DEAL_MAGIC))) continue;

      datetime closeTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
      if(closeTime <= g_lastReportedCloseTime) break;   // 已上报过

      outTickets[outCount]   = dealTicket;
      outPositions[outCount] = HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
      outCount++;

      if(closeTime > maxCloseTime) maxCloseTime = closeTime;
   }

   if(outCount == 0) return;

   // 第二遍：逐笔读取字段并反查开仓价（HistorySelectByPosition 会重置选择集，故放在第二遍）
   string trades = "";
   int    emitted = 0;

   for(int k = 0; k < outCount; k++)
   {
      ulong dealTicket = outTickets[k];
      long  positionId = outPositions[k];

      // 切换到该 position 的选择集：平仓 deal 本身也在其中，仍可按 ticket 读取
      if(!HistorySelectByPosition(positionId)) continue;

      string   symbol     = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
      long     magic      = HistoryDealGetInteger(dealTicket, DEAL_MAGIC);
      datetime closeTime  = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
      double   closePrice = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
      double   lots       = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
      double   netProfit  = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
                          + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
                          + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
      // 平仓 deal 的方向与持仓相反：DEAL_TYPE_SELL 平的是 BUY 仓
      string   side       = ((ENUM_DEAL_TYPE)HistoryDealGetInteger(dealTicket, DEAL_TYPE) == DEAL_TYPE_SELL) ? "BUY" : "SELL";

      if(StringLen(symbol) == 0) continue;

      // 反查开仓 deal
      double   openPrice = 0;
      datetime openTime  = closeTime;
      int      posDeals  = HistoryDealsTotal();
      for(int j = 0; j < posDeals; j++)
      {
         ulong inTicket = HistoryDealGetTicket(j);
         if(inTicket == 0) continue;
         if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(inTicket, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
         openPrice = HistoryDealGetDouble(inTicket, DEAL_PRICE);
         openTime  = (datetime)HistoryDealGetInteger(inTicket, DEAL_TIME);
         break;
      }

      if(emitted > 0) trades += ",";
      trades += StringFormat(
         "{\"account_id\":\"%s\",\"ticket\":%s,\"magic\":%s,\"symbol\":\"%s\","
         "\"side\":\"%s\",\"open_price\":%.5f,\"close_price\":%.5f,\"lots\":%.2f,"
         "\"profit\":%.2f,\"open_time\":\"%s\",\"close_time\":\"%s\"}",
         AccountID, FormatULongValue(dealTicket), FormatLongValue(magic), symbol,
         side, openPrice, closePrice, lots,
         netProfit,
         TimeToString(openTime, TIME_DATE|TIME_SECONDS),
         TimeToString(closeTime, TIME_DATE|TIME_SECONDS)
      );
      emitted++;
   }

   if(emitted == 0) return;

   string json = "{\"trades\":[" + trades + "]}";
   string resp = HttpPost("/api/trade_history", json);
   if(StringLen(resp) > 0 && StringFind(resp, "OK") >= 0)
   {
      g_lastReportedCloseTime = maxCloseTime;
      Print("📊 已上报平仓成交：", emitted, " 笔");
   }
}

// ============================================================
// 轮询并执行服务端指令
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

      Print("📨 收到 ", count, " 条指令");
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
         else if(action == "CLOSE_PARTIAL")
            ExecuteClosePartial(cmd, cmd_id);
         else if(action == "CLOSE_ALL")
            ExecuteCloseAll(cmd, cmd_id);
         else if(action == "OPEN")
            ExecuteOpen(cmd, cmd_id);
         else if(action == "ADD")
            ExecuteAdd(cmd, cmd_id);
         else
            Print("未知指令类型：", action);
      }
   }
}

// ============================================================
// 执行开仓指令 (用于价差交易)
// ============================================================
void ExecuteOpen(string cmd, string cmd_id)
{
   string symbol = GetJsonString(cmd, "symbol");
   string side   = GetJsonString(cmd, "side");
   double lots   = GetJsonDouble(cmd, "lots");
   string reason = GetJsonString(cmd, "reason");

   Print("🛢️ 价差开仓：", symbol, " ", side, " ", lots, "手 | ", reason);

   if(!EnableSpread)
   {
      Print("❌ 原油对冲套利未启用");
      ReportResult(cmd_id, "ERROR", 0, "spread_disabled");
      return;
   }

   if(!IsSpreadSymbol(symbol))
   {
      Print("❌ 非法价差腿品种：", symbol);
      ReportResult(cmd_id, "ERROR", 0, "spread_symbol_not_allowed");
      return;
   }

   if(!IsSymbolAvailable(symbol))
   {
      Print("❌ 品种不可用：", symbol);
      ReportResult(cmd_id, "ERROR", 0, "symbol_not_available");
      return;
   }

   if(side != "BUY" && side != "SELL")
   {
      Print("❌ 非法价差开仓方向：", side);
      ReportResult(cmd_id, "ERROR", 0, "invalid_side");
      return;
   }

   lots = NormalizeVolume(symbol, lots);
   string comment = "GB_SPREAD_" + reason;
   PrepareTrade(symbol, SpreadMagicNumber);

   bool result = false;
   ENUM_POSITION_TYPE posType = POSITION_TYPE_BUY;
   if(side == "BUY")
   {
      posType = POSITION_TYPE_BUY;
      result = trade.Buy(lots, symbol, 0.0, 0.0, 0.0, comment);
   }
   else if(side == "SELL")
   {
      posType = POSITION_TYPE_SELL;
      result = trade.Sell(lots, symbol, 0.0, 0.0, 0.0, comment);
   }

   if(TradeOperationSucceeded(result))
   {
      ulong rawTicket = (ulong)trade.ResultOrder();
      ulong ticket = ResolveLivePositionTicket(rawTicket, symbol, SpreadMagicNumber, posType);
      if(ticket == 0)
      {
         Print("⚠️ 价差开仓成交但未能解析实时持仓：order#", FormatULongValue(rawTicket), " ", symbol, " ", side, " ", lots, "手");
         ReportResult(cmd_id, "ERROR", (long)rawTicket, "position_resolve_incomplete");
         return;
      }

      Print("✅ 价差开仓成功：#", FormatULongValue(ticket), " ", symbol, " ", side, " ", lots, "手");
      ReportResult(cmd_id, "OK", (long)ticket, "");
   }
   else if(TradeOperationPartiallyFilled(result))
   {
      ulong rawTicket = (ulong)trade.ResultOrder();
      ulong ticket = ResolveLivePositionTicket(rawTicket, symbol, SpreadMagicNumber, posType);
      ulong reportTicket = ticket;
      if(reportTicket == 0)
         reportTicket = rawTicket;

      Print("⚠️ 价差开仓部分成交：#", FormatULongValue(reportTicket), " ", symbol, " ", side, " ", lots, "手");
      if(ticket == 0)
      {
         ReportResult(cmd_id, "ERROR", (long)reportTicket, "position_resolve_incomplete");
         return;
      }

      ReportResult(cmd_id, "ERROR", (long)ticket, "open_incomplete");
   }
   else
   {
      int err = GetTradeErrorCode();
      Print("❌ 价差开仓失败：Error#", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 执行加仓指令 (用于价差交易)
// ============================================================
void ExecuteAdd(string cmd, string cmd_id)
{
   ExecuteOpen(cmd, cmd_id);
}

// ============================================================
// 执行部分平仓指令 (用于价差交易)
// ============================================================
void ExecuteClosePartial(string cmd, string cmd_id)
{
   string symbol = GetJsonString(cmd, "symbol");
   double lots   = GetJsonDouble(cmd, "lots");
   string reason = GetJsonString(cmd, "reason");

   Print("🛢️ 价差部分平仓：", symbol, " ", lots, "手 | ", reason);

   if(!EnableSpread)
   {
      Print("❌ 原油对冲套利未启用");
      ReportResult(cmd_id, "ERROR", 0, "spread_disabled");
      return;
   }

   if(!IsSpreadSymbol(symbol))
   {
      Print("❌ 非法价差腿品种：", symbol);
      ReportResult(cmd_id, "ERROR", 0, "spread_symbol_not_allowed");
      return;
   }

   double remainingLots = lots;
   bool matchedPosition = false;
   bool closedAny = false;
   bool closeFailed = false;
   ulong lastTicket = 0;
   ulong failedTicket = 0;

   for(int i = PositionsTotal() - 1; i >= 0 && remainingLots > 0.0000001; i--)
   {
      if(!SelectPositionByIndex(i))
         continue;

      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;

       long magic = PositionGetInteger(POSITION_MAGIC);
       if(magic != SpreadMagicNumber)
          continue;

       matchedPosition = true;

        ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
        double positionVolumeBefore = PositionGetDouble(POSITION_VOLUME);
        double closeLots = MathMin(remainingLots, positionVolumeBefore);
        closeLots = NormalizeCloseVolume(symbol, closeLots);
        if(closeLots <= 0)
           continue;

      PrepareTrade(symbol, magic);

      bool result = trade.PositionClosePartial(ticket, closeLots, (ulong)Slippage);
       if(TradeOperationSucceeded(result))
       {
          remainingLots -= closeLots;
          remainingLots = MathMax(0.0, remainingLots);
          closedAny = true;
          lastTicket = ticket;
          Print("✅ 部分平仓成功：#", FormatULongValue(ticket), " ", symbol, " ", closeLots,
                "手 | 剩余=", DoubleToString(MathMax(0.0, remainingLots), 2));
       }
       else if(TradeOperationPartiallyFilled(result))
       {
          double filledLots = 0.0;
          if(PositionSelectByTicket(ticket))
             filledLots = NormalizeCloseVolume(symbol, MathMax(0.0, positionVolumeBefore - PositionGetDouble(POSITION_VOLUME)));

          if(filledLots > 0.0)
          {
             remainingLots -= filledLots;
             remainingLots = MathMax(0.0, remainingLots);
             closedAny = true;
             lastTicket = ticket;
          }

          Print("⚠️ 部分平仓部分成交：#", FormatULongValue(ticket), " ", symbol,
                " | 请求=", DoubleToString(closeLots, GetVolumeDigits(symbol)),
                "手 | 实际成交=", DoubleToString(filledLots, GetVolumeDigits(symbol)),
                "手 | 剩余请求=", DoubleToString(MathMax(0.0, remainingLots), GetVolumeDigits(symbol)), "手");
          ReportResult(cmd_id, "ERROR", (long)ticket, "partial_close_incomplete");
          return;
       }
       else
       {
          int err = GetTradeErrorCode();
          closeFailed = true;
          failedTicket = ticket;
          Print("❌ 部分平仓失败：#", FormatULongValue(ticket), " ", symbol, " ", closeLots,
                "手 | Error#", err);
          break;
       }
    }

    if(!matchedPosition)
    {
       Print("❌ 未找到对应持仓");
       ReportResult(cmd_id, "ERROR", 0, "position_not_found");
       return;
    }

    if(closeFailed)
    {
       ReportResult(cmd_id, "ERROR", (long)failedTicket, "close_failed");
       return;
    }

    if(remainingLots <= 0.0000001)
    {
       ReportResult(cmd_id, "OK", (long)lastTicket, "");
       return;
    }

   if(closedAny)
   {
      Print("⚠️ 部分平仓未完成：剩余 ", DoubleToString(MathMax(0.0, remainingLots), 2), " 手未成交");
       ReportResult(cmd_id, "ERROR", (long)lastTicket, "partial_close_incomplete");
       return;
    }

    Print("⚠️ 部分平仓未完成：请求手数无法完全执行，剩余 ", DoubleToString(MathMax(0.0, remainingLots), 2), " 手");
    ReportResult(cmd_id, "ERROR", 0, "partial_close_incomplete");
}

// ============================================================
// 执行全部平仓指令 (用于价差交易)
// ============================================================
void ExecuteCloseAll(string cmd, string cmd_id)
{
   string symbol = GetJsonString(cmd, "symbol");
   double lots   = GetJsonDouble(cmd, "lots");
   string reason = GetJsonString(cmd, "reason");

   Print("🛢️ 价差全部平仓：", symbol, " ", lots, "手 | ", reason);

   if(!EnableSpread)
   {
      Print("❌ 原油对冲套利未启用");
      ReportResult(cmd_id, "ERROR", 0, "spread_disabled");
      return;
   }

   if(!IsSpreadSymbol(symbol))
   {
      Print("❌ 非法价差腿品种：", symbol);
      ReportResult(cmd_id, "ERROR", 0, "spread_symbol_not_allowed");
      return;
   }

   int closedCount = 0;
   bool matchedPosition = false;
   bool closeFailed = false;
   bool closeIncomplete = false;
   ulong failedTicket = 0;
   ulong incompleteTicket = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!SelectPositionByIndex(i))
         continue;

      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;

       long magic = PositionGetInteger(POSITION_MAGIC);
       if(magic != SpreadMagicNumber)
          continue;

       matchedPosition = true;

       ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
        PrepareTrade(symbol, magic);

        bool result = trade.PositionClose(ticket, (ulong)Slippage);
       if(TradeOperationSucceeded(result))
        {
           Print("✅ 平仓成功：#", FormatULongValue(ticket), " ", symbol);
           closedCount++;
        }
       else if(TradeOperationPartiallyFilled(result))
       {
          closeIncomplete = true;
          incompleteTicket = ticket;
          Print("⚠️ 全部平仓未完成：#", FormatULongValue(ticket), " ", symbol, " | broker 部分成交，仍有剩余仓位");
       }
        else
        {
           int err = GetTradeErrorCode();
           closeFailed = true;
           failedTicket = ticket;
          Print("❌ 全部平仓失败：#", FormatULongValue(ticket), " ", symbol, " | Error#", err);
       }
    }

    if(!matchedPosition)
    {
       ReportResult(cmd_id, "ERROR", 0, "no_position_found");
       return;
    }

     if(closeFailed)
     {
        ReportResult(cmd_id, "ERROR", (long)failedTicket, "close_failed");
        return;
     }

     if(closeIncomplete)
     {
        ReportResult(cmd_id, "ERROR", (long)incompleteTicket, "close_incomplete");
        return;
     }

     if(closedCount > 0)
        ReportResult(cmd_id, "OK", closedCount, "");
}

// ============================================================
// Multi-TP 拆单辅助函数（MT5）
// ============================================================

// 拆分手数：60% 给 TP1（近目标，先落袋），40% 给 TP2（远目标，剩余）
bool SplitLotsForMultiTP_MT5(double totalLots, double &lotsTP1, double &lotsTP2, string brokerSymbol)
{
   lotsTP1 = 0;
   lotsTP2 = 0;

   if(totalLots <= 0)
      return false;

   double minLots  = SymbolInfoDouble(brokerSymbol, SYMBOL_VOLUME_MIN);
   double stepLots = SymbolInfoDouble(brokerSymbol, SYMBOL_VOLUME_STEP);
   if(stepLots <= 0) stepLots = 0.01;
   if(minLots <= 0) minLots = stepLots;

   int digits = GetVolumeDigits(brokerSymbol);
   double tolerance = MathMax(0.0000001, stepLots * 0.0001);
   double normalizedTotal = NormalizeDouble(MathFloor(totalLots / stepLots) * stepLots, digits);
   if(normalizedTotal > totalLots + tolerance)
      return false;
   if(normalizedTotal + tolerance < 2.0 * minLots)
      return false;

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

   if(lotsTP1 + lotsTP2 > normalizedTotal + tolerance || lotsTP1 + lotsTP2 > totalLots + tolerance)
   {
      lotsTP1 = 0;
      lotsTP2 = 0;
      return false;
   }

   return true;
}

// MT5 单订单开仓 + 设置 TP/SL（拆单模式）
bool OpenSingleOrderWithTP_MT5(double lots, double price, double sl, double tp,
                                string comment, int magic, ENUM_POSITION_TYPE posType,
                                ulong &outTicket, string brokerSymbol)
{
   outTicket = 0;
   double inputLots = lots;
   lots = NormalizeVolume(brokerSymbol, lots);
   double stepLots = SymbolInfoDouble(brokerSymbol, SYMBOL_VOLUME_STEP);
   if(stepLots <= 0) stepLots = 0.01;
   double tolerance = MathMax(0.0000001, stepLots * 0.0001);
   if(lots > inputLots + tolerance) return false;
   if(lots <= 0) return false;

   PrepareTrade(brokerSymbol, magic);
   bool result = false;
   if(posType == POSITION_TYPE_BUY)
      result = trade.Buy(lots, brokerSymbol, price, sl, tp, comment);
   else
      result = trade.Sell(lots, brokerSymbol, price, sl, tp, comment);

   if(!TradeOperationSucceeded(result))
   {
      Print("❌ 拆单开仓失败: Error#", GetTradeErrorCode());
      return false;
   }

   ulong rawTicket = (ulong)trade.ResultOrder();
   ulong ticket = ResolveLivePositionTicket(rawTicket, brokerSymbol, magic, posType);
   if(ticket == 0)
   {
      outTicket = rawTicket;
      Print("⚠️ 拆单成交但无法解析持仓 ticket");
      return false;
   }

   Print("✅ 拆单开仓: #", FormatULongValue(ticket), " ", EnumToString(posType),
         " ", DoubleToString(lots, 2), "手 @ ", DoubleToString(price, _Digits),
         " | SL=", DoubleToString(sl, _Digits), " TP=", DoubleToString(tp, _Digits),
         " | Magic=", magic);

   outTicket = ticket;
   return true;
}

// MT5 拆单开仓 (60% lots @ TP1, 40% lots @ TP2)
void ExecuteOpenWithTPSplit_MT5(string cmd, string cmd_id, string type_str, double lots,
                                double price, double sl, double tp1, double tp2,
                                string strategy, int score, int magicForOrder,
                                string commentBase, ENUM_POSITION_TYPE posType, string brokerSymbol)
{
   double lotsTP1 = 0, lotsTP2 = 0;
   if(!SplitLotsForMultiTP_MT5(lots, lotsTP1, lotsTP2, brokerSymbol))
   {
      Print("⚠️ 拆单手数无效，退回单订单 TP2，totalLots=", DoubleToString(lots, 2));
      ulong fallbackTicket = 0;
      bool fallbackOK = OpenSingleOrderWithTP_MT5(lots, price, sl, tp2,
                                                   commentBase, magicForOrder, posType,
                                                   fallbackTicket, brokerSymbol);
      if(fallbackOK)
         ReportResult(cmd_id, "OK", (long)fallbackTicket, "single_tp2_after_split_invalid");
      else
         ReportResult(cmd_id, "ERROR", (long)fallbackTicket, "single_tp2_after_split_failed");
      return;
   }

   // 订单 A: TP1
   ulong ticketA = 0;
   bool okA = OpenSingleOrderWithTP_MT5(lotsTP1, price, sl, tp1,
                                         commentBase + "_A", magicForOrder, posType, ticketA, brokerSymbol);

   // 订单 B: TP2
   ulong ticketB = 0;
   bool okB = OpenSingleOrderWithTP_MT5(lotsTP2, price, sl, tp2,
                                         commentBase + "_B", magicForOrder, posType, ticketB, brokerSymbol);

   if(okA && okB)
   {
      Print("✅ 拆单成功: TP1=#", FormatULongValue(ticketA), " (", DoubleToString(lotsTP1, 2), "手) | ",
            "TP2=#", FormatULongValue(ticketB), " (", DoubleToString(lotsTP2, 2), "手) | ",
            "合计=", DoubleToString(lotsTP1 + lotsTP2, 2), "手");
      ReportResult(cmd_id, "OK", (long)ticketA,
                   "split;A=" + FormatULongValue(ticketA) + "_" + DoubleToString(lotsTP1, 2) +
                   ";B=" + FormatULongValue(ticketB) + "_" + DoubleToString(lotsTP2, 2));
   }
   else if(okA && !okB)
   {
      Print("⚠️ 拆单部分成功: A=#", FormatULongValue(ticketA), " (TP1), B 失败");
      ReportResult(cmd_id, "PARTIAL", (long)ticketA,
                   "split;A_ok=" + FormatULongValue(ticketA) + ";B_failed_err=" + IntegerToString(GetTradeErrorCode()));
   }
   else if(!okA && okB)
   {
      Print("⚠️ 拆单部分成功: A 失败, B=#", FormatULongValue(ticketB), " (TP2)");
      ReportResult(cmd_id, "PARTIAL", (long)ticketB,
                   "split;A_failed;B_ok=" + FormatULongValue(ticketB));
   }
   else
   {
      Print("❌ 拆单全部失败");
      ReportResult(cmd_id, "ERROR", 0, "split_all_failed");
   }
}

// ============================================================
// 执行开仓信号（风控在本地，策略在服务端）
// ============================================================
void ExecuteSignal(string cmd, string cmd_id)
{
   // 挂单类信号（限价/止损单）转交 ExecutePending（与 MT4 行为一致）
   string orderType = GetJsonStringSafe(cmd, "order_type");
   if(orderType == "BUY_LIMIT" || orderType == "BUY_STOP" ||
      orderType == "SELL_LIMIT" || orderType == "SELL_STOP")
   {
      ExecutePending(cmd, cmd_id);
      return;
   }

   string symbol   = GetJsonStringSafe(cmd, "symbol");
   string type_str = GetJsonStringSafe(cmd, "type");
   double sl       = GetJsonDouble(cmd, "sl");
   double tp1      = GetJsonDouble(cmd, "tp1");
   if(tp1 == 0.0) tp1 = GetJsonDouble(cmd, "tp"); // 兼容
   double tp2      = GetJsonDouble(cmd, "tp2");    // Multi-TP 拆单
   bool   tpSplit  = GetJsonBool(cmd, "tp_split"); // Multi-TP 拆单标志
   int    score    = GetJsonInt(cmd, "score");
   string strategy = GetJsonStringSafe(cmd, "strategy");

   // 确定品种：使用信号自带的品种，否则用第一个配置品种
   string baseSymbol = symbol;
   if(StringLen(baseSymbol) == 0)
      baseSymbol = g_symbols[0];
   string brokerSymbol = GetBrokerSymbol(baseSymbol);

   Print("📡 信号：", type_str, " | 品种=", baseSymbol, " → ", brokerSymbol,
         " | SL=", sl, " TP=", tp1, " | ", strategy, " 评分:", score);

   if(StringLen(symbol) > 0 && !IsPrimarySymbol(symbol))
   {
      Print("❌ 信号品种不在配置列表：", symbol, " | 已配置: ", Symbols);
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

   if(!CheckRisk(type_str, baseSymbol))
   {
      ReportResult(cmd_id, "REJECTED", 0, "risk_check_failed");
      return;
   }

   double price = 0.0;
   ENUM_POSITION_TYPE posType = POSITION_TYPE_BUY;
   if(type_str == "BUY")
   {
      posType = POSITION_TYPE_BUY;
      price = SymbolInfoDouble(brokerSymbol, SYMBOL_ASK);
   }
   else if(type_str == "SELL")
   {
      posType = POSITION_TYPE_SELL;
      price = SymbolInfoDouble(brokerSymbol, SYMBOL_BID);
   }

   double sl_distance = MathAbs(price - sl);
   double lots = CalcLotsForStrategy(strategy, baseSymbol, sl_distance);
   lots = NormalizeVolume(brokerSymbol, lots);
   string comment = "GB_" + strategy + "_S" + IntegerToString(score);

   // Multi-TP 拆单: 检查是否需要拆成两个订单
   if(tpSplit && tp2 > 0 && MathAbs(tp1 - tp2) > _Point)
   {
      ExecuteOpenWithTPSplit_MT5(cmd, cmd_id, type_str, lots, price, sl, tp1, tp2,
                                  strategy, score, magicForOrder, comment, posType, brokerSymbol);
      return; // 拆单模式独立处理
   }

   PrepareTrade(brokerSymbol, magicForOrder);

   bool result = false;
   if(type_str == "BUY")
      result = trade.Buy(lots, brokerSymbol, 0.0, 0.0, 0.0, comment);
   else
      result = trade.Sell(lots, brokerSymbol, 0.0, 0.0, 0.0, comment);

   ulong rawTicket = (ulong)trade.ResultOrder();

   if(TradeOperationSucceeded(result))
   {
      ulong ticket = ResolveLivePositionTicket(rawTicket, brokerSymbol, magicForOrder, posType);
      if(ticket == 0)
      {
         Print("⚠️ 开仓成交但未能解析实时持仓：order#", FormatULongValue(rawTicket), " ", type_str, " ", lots, "手");
         ReportResult(cmd_id, "ERROR", (long)rawTicket, "position_resolve_incomplete");
         return;
      }

      string protectionStatus = EnsureSignalProtectionAttached(ticket, type_str, sl, tp1);
      if(protectionStatus != "")
      {
         ReportResult(cmd_id, "ERROR", (long)ticket, protectionStatus);
         return;
      }

      Print("✅ 开仓：#", FormatULongValue(ticket), " ", type_str, " ", lots, "手 @ ", price,
            " | Magic=", magicForOrder, " (", strategy, ")");
      ReportResult(cmd_id, "OK", (long)ticket, "");

      // === 加仓后统一修改已有同方向仓位的 SL/TP（与 MT4 行为一致）===
      double unifiedSL = GetJsonDouble(cmd, "unified_sl");
      if(unifiedSL > 0)
      {
         Print("📐 加仓统一SL: ", unifiedSL, " → 修改已有 ", type_str, " 仓位");
         int synced = 0;
         double minDist = (double)SymbolInfoInteger(brokerSymbol, SYMBOL_TRADE_STOPS_LEVEL) * GetSymbolPoint(brokerSymbol);
         for(int j = PositionsTotal() - 1; j >= 0; j--)
         {
            if(!SelectPositionByIndex(j)) continue;
            if(PositionGetString(POSITION_SYMBOL) != brokerSymbol) continue;
            if(PositionGetInteger(POSITION_MAGIC) != magicForOrder) continue;

            ulong existTicket = (ulong)PositionGetInteger(POSITION_TICKET);
            if(existTicket == ticket) continue; // 跳过刚开的新仓

            ENUM_POSITION_TYPE existingType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
            if((type_str == "BUY" && existingType != POSITION_TYPE_BUY) ||
               (type_str == "SELL" && existingType != POSITION_TYPE_SELL))
               continue;

            double existSL = PositionGetDouble(POSITION_SL);
            double existTP = PositionGetDouble(POSITION_TP);
            double openPx  = PositionGetDouble(POSITION_PRICE_OPEN);
            double newSL   = unifiedSL;
            double newTP   = tp1; // 统一 TP1

            // 确保 SL 距离符合 broker 最小要求
            if(minDist > 0)
            {
               if(type_str == "BUY" && MathAbs(openPx - newSL) < minDist)
                  newSL = openPx - minDist;
               if(type_str == "SELL" && MathAbs(newSL - openPx) < minDist)
                  newSL = openPx + minDist;
            }

            // SL 只能向盈利方向移动（BUY: new >= old, SELL: new <= old）
            bool slOK = false;
            if(type_str == "BUY" && newSL >= existSL)  slOK = true;
            if(type_str == "SELL" && newSL <= existSL) slOK = true;

            if(slOK && (newSL != existSL || newTP != existTP))
            {
               PrepareTrade(brokerSymbol, magicForOrder);
               if(TradeOperationSucceeded(trade.PositionModify(existTicket, newSL, newTP)))
               {
                  synced++;
                  Print("  ✅ #", FormatULongValue(existTicket), " SL: ", existSL, "→", newSL,
                        " TP: ", existTP, "→", newTP);
               }
               else
               {
                  Print("  ⚠️ #", FormatULongValue(existTicket), " 改单失败: ", GetTradeErrorCode());
               }
            }
         }
         if(synced > 0)
            Print("📐 加仓统一SL完成: 同步 ", synced, " 个仓位");
      }
   }
   else if(TradeOperationPartiallyFilled(result))
   {
      ulong ticket = ResolveLivePositionTicket(rawTicket, brokerSymbol, magicForOrder, posType);
      ulong reportTicket = ticket;
      if(reportTicket == 0)
         reportTicket = rawTicket;

      Print("⚠️ 开仓部分成交：#", FormatULongValue(reportTicket), " ", type_str, " ", lots, "手 @ ", price,
            " | Magic=", magicForOrder, " (", strategy, ")");

      if(ticket == 0)
      {
         ReportResult(cmd_id, "ERROR", (long)reportTicket, "position_resolve_incomplete");
         return;
      }

      string protectionStatus = EnsureSignalProtectionAttached(ticket, type_str, sl, tp1);
      if(protectionStatus != "")
      {
         ReportResult(cmd_id, "ERROR", (long)ticket, protectionStatus);
         return;
      }

      ReportResult(cmd_id, "ERROR", (long)ticket, "open_incomplete");
      return;
   }
   else
   {
      int err = GetTradeErrorCode();
      Print("❌ 开仓失败：Error#", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 执行挂单指令（限价/止损单，与 MT4 ExecutePending 对齐）
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
   double tp2          = GetJsonDouble(cmd, "tp2");    // Multi-TP 拆单: TP2
   bool   tpSplit      = GetJsonBool(cmd, "tp_split"); // Multi-TP 拆单标志
   int    score        = GetJsonInt(cmd, "score");
   string strategy     = GetJsonStringSafe(cmd, "strategy");
   datetime expiration = (datetime)GetJsonDouble(cmd, "expiration");

   // 确定品种：使用信号自带的品种，否则用第一个配置品种
   string baseSymbol = signalSymbol;
   if(StringLen(baseSymbol) == 0)
      baseSymbol = g_symbols[0];
   string brokerSymbol = GetBrokerSymbol(baseSymbol);

   Print("📡 挂单信号：", type_str, " | 品种=", baseSymbol, " → ", brokerSymbol,
         " | 入场=", entry, " SL=", sl, " TP=", tp1, " | ", strategy, " 评分:", score);

   if(StringLen(signalSymbol) > 0 && !IsPrimarySymbol(signalSymbol))
   {
      Print("❌ 挂单品种不在配置列表：", signalSymbol, " | 已配置: ", Symbols);
      ReportResult(cmd_id, "ERROR", 0, "symbol_mismatch");
      return;
   }

   if(type_str != "BUY" && type_str != "SELL")
   {
      Print("❌ 非法挂单方向：", type_str);
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

   double point = GetSymbolPoint(brokerSymbol);

   // 确定挂单类型
   ENUM_ORDER_TYPE pendingType = ORDER_TYPE_BUY_LIMIT;
   if(orderType == "BUY_LIMIT")
      pendingType = ORDER_TYPE_BUY_LIMIT;
   else if(orderType == "BUY_STOP")
      pendingType = ORDER_TYPE_BUY_STOP;
   else if(orderType == "SELL_LIMIT")
      pendingType = ORDER_TYPE_SELL_LIMIT;
   else if(orderType == "SELL_STOP")
      pendingType = ORDER_TYPE_SELL_STOP;
   else
   {
      // 自动检测：entry 与当前价格的相对位置
      if(type_str == "BUY")
      {
         double ask = SymbolInfoDouble(brokerSymbol, SYMBOL_ASK);
         if(entry <= ask + 5 * point)
            pendingType = ORDER_TYPE_BUY_LIMIT;  // 低于或接近市价 → 限价买入
         else
            pendingType = ORDER_TYPE_BUY_STOP;   // 高于市价 → 止损买入
      }
      else // SELL
      {
         double bid = SymbolInfoDouble(brokerSymbol, SYMBOL_BID);
         if(entry >= bid - 5 * point)
            pendingType = ORDER_TYPE_SELL_LIMIT; // 高于或接近市价 → 限价卖出
         else
            pendingType = ORDER_TYPE_SELL_STOP;  // 低于市价 → 止损卖出
      }
   }

   // 检查重复挂单（同品种、同方向、同magic、价格相近）
   bool newIsBuy = (pendingType == ORDER_TYPE_BUY_LIMIT || pendingType == ORDER_TYPE_BUY_STOP);
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ordTicket = OrderGetTicket(i);
      if(ordTicket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != brokerSymbol) continue;
      if(OrderGetInteger(ORDER_MAGIC) != magicForOrder) continue;

      ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      bool isBuyPending  = (ot == ORDER_TYPE_BUY_LIMIT || ot == ORDER_TYPE_BUY_STOP);
      bool isSellPending = (ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP);
      if(!isBuyPending && !isSellPending) continue;

      if((newIsBuy && isBuyPending) || (!newIsBuy && isSellPending))
      {
         // 价格过于接近（10 点以内）视为重复挂单
         double existingPrice = OrderGetDouble(ORDER_PRICE_OPEN);
         if(MathAbs(existingPrice - entry) < 10 * point)
         {
            Print("❌ 已有相近价格挂单：", brokerSymbol, " 现有=", existingPrice, " 新=", entry);
            ReportResult(cmd_id, "ERROR", 0, "duplicate_pending");
            return;
         }
      }
   }

   // 计算手数：挂单始终使用 EA 策略配置，不使用服务端 cmd.lots 覆盖。
   double currentPrice = (type_str == "BUY") ? SymbolInfoDouble(brokerSymbol, SYMBOL_ASK)
                                             : SymbolInfoDouble(brokerSymbol, SYMBOL_BID);
   double sl_distance = MathAbs(currentPrice - sl);
   double lots = CalcLotsForStrategy(strategy, baseSymbol, sl_distance);
   lots = NormalizeVolume(brokerSymbol, lots);

   // 挂单同样需要通过本地风控检查
   if(!CheckRisk(type_str, baseSymbol))
   {
      Print("❌ 风控拒绝挂单：", type_str, " ", baseSymbol);
      ReportResult(cmd_id, "REJECTED", 0, "risk_check_failed");
      return;
   }

   string comment = "GB_" + strategy + "_S" + IntegerToString(score);

   // 过期时间：默认24小时；broker 不支持指定过期时间则回退 GTC
   if(expiration <= 0)
      expiration = TimeCurrent() + 24 * 60 * 60;
   ENUM_ORDER_TYPE_TIME timeType = ORDER_TIME_SPECIFIED;
   long expModes = SymbolInfoInteger(brokerSymbol, SYMBOL_EXPIRATION_MODE);
   if((expModes & SYMBOL_EXPIRATION_SPECIFIED) == 0)
   {
      timeType = ORDER_TIME_GTC;
      expiration = 0;
   }

   // SL/TP 满足最小止损距离（MT5 挂单 SL/TP 随下单请求提交，非法距离会导致整单被拒）
   double min_stop = (double)SymbolInfoInteger(brokerSymbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double final_sl = sl, final_tp1 = tp1, final_tp2 = tp2;
   if(min_stop > 0)
   {
      if(sl > 0 && MathAbs(entry - sl) < min_stop)
         final_sl = (type_str == "BUY") ? entry - min_stop : entry + min_stop;
      if(tp1 > 0 && MathAbs(tp1 - entry) < min_stop)
         final_tp1 = (type_str == "BUY") ? entry + min_stop : entry - min_stop;
      if(tp2 > 0 && MathAbs(tp2 - entry) < min_stop)
         final_tp2 = (type_str == "BUY") ? entry + min_stop : entry - min_stop;
   }

   PrepareTrade(brokerSymbol, magicForOrder);
   bool pendingSplitFallback = false;

   // Multi-TP 拆单: 挂单模式同样支持（两个挂单，同入场价不同 TP）
   if(tpSplit && tp2 > 0 && MathAbs(tp1 - tp2) > _Point)
   {
      double lotsTP1 = 0, lotsTP2 = 0;
      bool splitLotsOK = SplitLotsForMultiTP_MT5(lots, lotsTP1, lotsTP2, brokerSymbol);
      if(splitLotsOK)
      {
         ulong ticketA = 0, ticketB = 0;
         if(TradeOperationSucceeded(trade.OrderOpen(brokerSymbol, pendingType, lotsTP1, 0.0, entry,
                                                    final_sl, final_tp1, timeType, expiration,
                                                    comment + "_A")))
            ticketA = (ulong)trade.ResultOrder();

         if(TradeOperationSucceeded(trade.OrderOpen(brokerSymbol, pendingType, lotsTP2, 0.0, entry,
                                                    final_sl, final_tp2, timeType, expiration,
                                                    comment + "_B")))
            ticketB = (ulong)trade.ResultOrder();

         if(ticketA > 0 && ticketB > 0)
         {
            Print("✅ 拆单挂单成功: TP1=#", FormatULongValue(ticketA), " (", DoubleToString(lotsTP1, 2), "手) | ",
                  "TP2=#", FormatULongValue(ticketB), " (", DoubleToString(lotsTP2, 2), "手)");
            ReportResult(cmd_id, "OK", (long)ticketA,
                         "split_pending;A=" + FormatULongValue(ticketA) + "_" + DoubleToString(lotsTP1, 2) +
                         ";B=" + FormatULongValue(ticketB) + "_" + DoubleToString(lotsTP2, 2));
         }
         else if(ticketA > 0 || ticketB > 0)
         {
            Print("⚠️ 拆单挂单部分失败: A=", FormatULongValue(ticketA), " B=", FormatULongValue(ticketB));
            ReportResult(cmd_id, "PARTIAL", (long)(ticketA > 0 ? ticketA : ticketB),
                         "split_pending;A=" + FormatULongValue(ticketA) + ";B=" + FormatULongValue(ticketB));
         }
         else
         {
            Print("❌ 拆单挂单全部失败: Error#", GetTradeErrorCode());
            ReportResult(cmd_id, "ERROR", 0, "split_all_failed");
         }
         return;
      }

      Print("⚠️ 拆单挂单手数无效，退回单挂单 TP2，totalLots=", DoubleToString(lots, 2));
      final_tp1 = final_tp2;
      pendingSplitFallback = true;
   }

   bool sent = trade.OrderOpen(brokerSymbol, pendingType, lots, 0.0, entry,
                               final_sl, final_tp1, timeType, expiration, comment);
   if(TradeOperationSucceeded(sent))
   {
      ulong ticket = (ulong)trade.ResultOrder();
      Print("✅ 挂单成功：#", FormatULongValue(ticket), " ", type_str, " ", lots, "手 @ ", entry,
            " | Magic=", magicForOrder, " (", strategy, ")");
      ReportResult(cmd_id, "OK", (long)ticket, pendingSplitFallback ? "single_tp2_after_split_invalid" : "");
   }
   else
   {
      int err = GetTradeErrorCode();
      Print("❌ 挂单失败：Error#", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 取消挂单指令
// ============================================================
void ExecuteCancelPending(string cmd, string cmd_id)
{
   ulong ticket = (ulong)GetJsonDouble(cmd, "ticket");
   string reason = GetJsonStringSafe(cmd, "reason");

   if(ticket == 0)
   {
      Print("❌ 取消挂单：无效ticket");
      ReportResult(cmd_id, "ERROR", 0, "invalid_ticket");
      return;
   }

   if(!OrderSelect(ticket))
   {
      Print("❌ 取消挂单：找不到订单 #", FormatULongValue(ticket));
      ReportResult(cmd_id, "ERROR", 0, "order_not_found");
      return;
   }

   ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
   if(ot != ORDER_TYPE_BUY_LIMIT && ot != ORDER_TYPE_BUY_STOP &&
      ot != ORDER_TYPE_SELL_LIMIT && ot != ORDER_TYPE_SELL_STOP)
   {
      Print("❌ 取消挂单：#", FormatULongValue(ticket), " 不是挂单（", (int)ot, "）");
      ReportResult(cmd_id, "ERROR", 0, "not_pending");
      return;
   }

   if(!IsOurMagic(OrderGetInteger(ORDER_MAGIC)))
   {
      Print("❌ 取消挂单：#", FormatULongValue(ticket), " 不属于本 EA");
      ReportResult(cmd_id, "ERROR", 0, "order_not_owned");
      return;
   }

   if(TradeOperationSucceeded(trade.OrderDelete(ticket)))
   {
      Print("🗑️ 取消挂单：#", FormatULongValue(ticket), " | ", reason);
      ReportResult(cmd_id, "OK", (long)ticket, "");
   }
   else
   {
      int err = GetTradeErrorCode();
      Print("❌ 取消挂单失败：#", FormatULongValue(ticket), " Error#", err);
      ReportResult(cmd_id, "ERROR", (long)ticket, IntegerToString(err));
   }
}

// ============================================================
// 执行改单指令（服务端决定止损止盈值）
// ============================================================
void ExecuteModify(string cmd, string cmd_id)
{
   ulong ticket = (ulong)GetJsonDouble(cmd, "ticket");
   
   // 兼容两种字段名：new_sl（AI 止损）或 sl（传统）
   double sl = GetJsonDouble(cmd, "new_sl");
   if(sl == 0.0) sl = GetJsonDouble(cmd, "sl");
   
   // TP 保持原值，服务端不修改 TP
   double tp = GetJsonDouble(cmd, "tp");
   
   Print("📝 改单：#", FormatULongValue(ticket), " SL=", sl, " TP=", tp);

   if(!PositionSelectByTicket(ticket))
   {
      // 市价仓中找不到 → 尝试挂单（挂单用 OrderModify 修改）
      if(OrderSelect(ticket))
      {
         string sym = OrderGetString(ORDER_SYMBOL);
         if(!IsAllowedSymbol(sym))
         {
            Print("❌ 订单品种不属于本实例：", sym);
            ReportResult(cmd_id, "ERROR", 0, "symbol_not_allowed");
            return;
         }

         if(!IsOurMagic(OrderGetInteger(ORDER_MAGIC)))
         {
            Print("❌ 订单不属于本 EA：Magic=", FormatLongValue(OrderGetInteger(ORDER_MAGIC)));
            ReportResult(cmd_id, "ERROR", 0, "order_not_owned");
            return;
         }

         PrepareTrade(sym, OrderGetInteger(ORDER_MAGIC));
         bool result = trade.OrderModify(ticket, OrderGetDouble(ORDER_PRICE_OPEN), sl, tp, ORDER_TIME_GTC, 0);
         if(TradeOperationSucceeded(result))
         {
            Print("✅ 改单成功（挂单）");
            ReportResult(cmd_id, "OK", (long)ticket, "");
         }
         else
         {
            int err = GetTradeErrorCode();
            Print("❌ 改单失败（挂单）：", err);
            ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
         }
         return;
      }

      Print("❌ 未找到订单 #", FormatULongValue(ticket));
      ReportResult(cmd_id, "ERROR", 0, "order_not_found");
      return;
   }

   string symbol = PositionGetString(POSITION_SYMBOL);
   if(!IsAllowedSymbol(symbol))
   {
      Print("❌ 订单品种不属于本实例：", symbol);
      ReportResult(cmd_id, "ERROR", 0, "symbol_not_allowed");
      return;
   }

   if(!IsOurMagic(PositionGetInteger(POSITION_MAGIC)))
   {
      Print("❌ 订单不属于本 EA：Magic=", FormatLongValue(PositionGetInteger(POSITION_MAGIC)));
      ReportResult(cmd_id, "ERROR", 0, "order_not_owned");
      return;
   }

   PrepareTrade(symbol, PositionGetInteger(POSITION_MAGIC));
   bool result = trade.PositionModify(ticket, sl, tp);
   if(TradeOperationSucceeded(result))
   {
      Print("✅ 改单成功");
      ReportResult(cmd_id, "OK", (long)ticket, "");
   }
   else
   {
      int err = GetTradeErrorCode();
      Print("❌ 改单失败：", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 执行平仓指令
// ============================================================
void ExecuteClose(string cmd, string cmd_id)
{
   ulong ticket = (ulong)GetJsonDouble(cmd, "ticket");
   string reason = GetJsonStringSafe(cmd, "reason");

   Print("📤 平仓：#", FormatULongValue(ticket), " | ", reason);

   if(!PositionSelectByTicket(ticket))
   {
      // 检查是否为挂单（挂单必须走 CANCEL_PENDING，禁止直接平仓）
      if(OrderSelect(ticket))
      {
         ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         if(IsPendingOrderType((int)ot))
         {
            Print("❌ 平仓拒绝：#", FormatULongValue(ticket), " 是挂单 ", OrderTypeToString((int)ot), "，应使用 CANCEL_PENDING");
            ReportResult(cmd_id, "ERROR", 0, "not_market");
            return;
         }
      }

      Print("❌ 未找到订单 #", FormatULongValue(ticket));
      ReportResult(cmd_id, "ERROR", 0, "order_not_found");
      return;
   }

   string sym = PositionGetString(POSITION_SYMBOL);
   if(!IsAllowedSymbol(sym))
   {
      Print("❌ 订单品种不属于本实例：", sym);
      ReportResult(cmd_id, "ERROR", 0, "symbol_not_allowed");
      return;
   }

   if(!IsOurMagic(PositionGetInteger(POSITION_MAGIC)))
   {
      Print("❌ 订单不属于本 EA：Magic=", FormatLongValue(PositionGetInteger(POSITION_MAGIC)));
      ReportResult(cmd_id, "ERROR", 0, "order_not_owned");
      return;
   }

   long magic = PositionGetInteger(POSITION_MAGIC);
   PrepareTrade(sym, magic);

   // 读取服务端指定的平仓手数（用于 TP1/TP2 分批平仓）。
   // 若未指定或指定值 >= 总手数，则全平。
   double cmdLots   = GetJsonDouble(cmd, "lots");
   double totalLots = PositionGetDouble(POSITION_VOLUME);
   bool   partial   = false;
   double closeLots = totalLots;
   if(cmdLots > 0.0009 && cmdLots < totalLots - 0.0001)
   {
      double normalized = NormalizeCloseVolume(sym, cmdLots);
      if(normalized > 0 && normalized < totalLots - 0.0000001)
      {
         closeLots = normalized;
         partial = true;
      }
   }

   Print("📦 平仓手数：指令=", cmdLots, " 持仓=", totalLots, " 执行=", closeLots);

   bool result = partial ? trade.PositionClosePartial(ticket, closeLots, (ulong)Slippage)
                         : trade.PositionClose(ticket, (ulong)Slippage);
   if(TradeOperationSucceeded(result))
   {
      Print("✅ 平仓成功");
      ReportResult(cmd_id, "OK", (long)ticket, "");
   }
   else if(TradeOperationPartiallyFilled(result))
   {
      Print("⚠️ 平仓未完成：#", FormatULongValue(ticket), " | broker 部分成交，仍有剩余仓位");
      ReportResult(cmd_id, "ERROR", (long)ticket, "close_incomplete");
   }
   else
   {
      int err = GetTradeErrorCode();
      Print("❌ 平仓失败：", err);
      ReportResult(cmd_id, "ERROR", 0, IntegerToString(err));
   }
}

// ============================================================
// 检查本地风控
// ============================================================
bool CheckRisk(string type_str, string baseSymbol)
{
   string brokerSymbol = GetBrokerSymbol(baseSymbol);
   double currentSpread = GetCurrentSpreadPoints(brokerSymbol);
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

   int sameDir = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!SelectPositionByIndex(i))
         continue;

      if(!IsPrimarySymbol(PositionGetString(POSITION_SYMBOL)))
          continue;

      if(!IsOurMagic(PositionGetInteger(POSITION_MAGIC)))
         continue;

      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if((type_str == "BUY" && posType == POSITION_TYPE_BUY) ||
         (type_str == "SELL" && posType == POSITION_TYPE_SELL))
      {
         sameDir++;
      }
   }

   if(sameDir >= MaxSameDir)
   {
      Print("⚠️ 风控：同方向持仓达到上限 ", MaxSameDir);
      return false;
   }

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double dailyPnL = equity - dailyStartEquity;
   double dailyPnL_pct = 0.0;
   if(dailyStartEquity > 0)
      dailyPnL_pct = (dailyPnL / dailyStartEquity) * 100.0;

   if(dailyPnL_pct < -MaxDailyLoss)
   {
      Print("⚠️ 风控：日亏损达到 ", DoubleToString(-dailyPnL_pct, 2), "% > ", MaxDailyLoss, "%");
      return false;
   }

   double totalProfit = 0.0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!SelectPositionByIndex(i))
         continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      if(!IsAllowedSymbol(symbol))
         continue;

      if(!IsOurMagic(PositionGetInteger(POSITION_MAGIC)))
         continue;

      totalProfit += PositionGetDouble(POSITION_PROFIT);
   }

   double floatLoss_pct = 0.0;
   if(equity > 0)
      floatLoss_pct = (totalProfit / equity) * 100.0;

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
// tradeSymbol: 基础品种名（如 XAUUSD / US100），用于 map 查找
// brokerSymbol: 经纪商实际下单品种（含后缀），用于 volume 规范化
// applySymbolMap: 是否套用 SymbolLotsMap（策略独立手数如 momentum_scalp 应传 false）
double CalcLotsWithConfig(bool useFixedLots, double fixedLots, double riskPercent,
                          double sl_distance, string tradeSymbol, string brokerSymbol, bool applySymbolMap = true)
{
   if(StringLen(brokerSymbol) == 0)
      brokerSymbol = tradeSymbol;
   if(StringLen(brokerSymbol) == 0)
      brokerSymbol = GetBrokerSymbol(g_symbols[0]);
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

   double riskAmount = AccountInfoDouble(ACCOUNT_EQUITY) * (riskPercent / 100.0);
   double tickValue = SymbolInfoDouble(brokerSymbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(brokerSymbol, SYMBOL_TRADE_TICK_SIZE);

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
      tradeSymbol = g_symbols[0];
      brokerSymbol = GetBrokerSymbol(g_symbols[0]);
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
   if(!SymbolSelect(sym, true))
      return false;

   return (SymbolInfoDouble(sym, SYMBOL_BID) > 0);
}

// ============================================================
// 报告指令执行结果给服务端
// ============================================================
void ReportResult(string cmd_id, string result, long ticket, string error)
{
   string json = "{\"account_id\":\"" + AccountID +
                 "\",\"command_id\":\"" + cmd_id +
                 "\",\"result\":\"" + result +
                 "\",\"ticket\":" + FormatLongValue(ticket) +
                 ",\"error\":\"" + error + "\"}";

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
   string headers = "Content-Type: application/json\r\n";
   if(ApiToken != "")
      headers += "X-API-Token: " + ApiToken + "\r\n";

   string result_headers = "";
   int timeout = httpTimeout;

   int code = WebRequest("POST", url, headers, timeout, post_data, result_data, result_headers);
   if(code >= 200 && code < 300)
   {
      gbConnected = true;
      lastSuccessTime = TimeCurrent();
      failCount = 0;
      return CharArrayToString(result_data);
   }

   // 第一次失败，立即重试一次（不再 Sleep，避免阻塞图表线程；若仍失败由调用方下一 tick 再试）
   result_headers = "";
   code = WebRequest("POST", url, headers, timeout, post_data, result_data, result_headers);

   if(code >= 200 && code < 300)
   {
      gbConnected = true;
      lastSuccessTime = TimeCurrent();
      failCount = 0;
      return CharArrayToString(result_data);
   }

   failCount++;
   if(failCount >= 3 && gbConnected)
   {
      gbConnected = false;
      Print("⚠️ GB Server 断连 | 失败次数：", failCount, " | 路径：", path);
   }

   return "";
}

// ============================================================
// Visual bridge HTTP POST（单次请求，不重试、不 Sleep）
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
      string latest = GetJsonString(resp, "latest_version");
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
// 可视化桥接（/visual/poll + 本地缓存文件）
// ============================================================
string SanitizeVisualFilePart(string value)
{
   string sanitized = value;
   StringTrimLeft(sanitized);
   StringTrimRight(sanitized);
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

      StringTrimLeft(token);
      StringTrimRight(token);
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
   json += "\"client\":\"mt5_visual_bridge\"";
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

   string num_str = "";
   for(int i = 0; i < StringLen(rest); i++)
   {
      ushort c = StringGetCharacter(rest, i);
      if((c >= 48 && c <= 57) || c == 46 || c == 45 || c == 101 || c == 69 || c == 43)
         num_str += StringSubstr(rest, i, 1);
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
      ushort c = StringGetCharacter(json, i);
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

string GetArrayElement(string array_str, int index)
{
   int brace_count = 0;
   int start = -1;
   int current_index = 0;

   for(int i = 0; i < StringLen(array_str); i++)
   {
      ushort c = StringGetCharacter(array_str, i);
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
      ushort c = StringGetCharacter(json, i);

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
//| 安全的 JSON 字符串转义（写入 JSON 前使用）                          |
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
      ushort c = StringGetCharacter(json, i);

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
