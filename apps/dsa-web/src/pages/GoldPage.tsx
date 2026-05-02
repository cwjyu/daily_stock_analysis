import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CircleDollarSign, RefreshCw, TrendingUp, TrendingDown, Minus, Bot } from 'lucide-react';
import {
  ComposedChart, Line, Bar, Area, XAxis, YAxis, Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Button, Card, Loading, ApiErrorAlert } from '../components/common';
import { agentApi } from '../api/agent';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import apiClient from '../api/index';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */
interface CommodityQuote {
  code: string;
  name: string;
  price: number;
  prev_close: number;
  change: number;
  change_pct: number;
  update_time: string;
  rmb_per_gram?: number;
  usdcny_rate?: number;
}

interface GoldAnalysis {
  current_price: number;
  trend: string;
  trend_strength: string;
  ma_alignment: string;
  adx: number;
  plus_di: number;
  minus_di: number;
  bb_position: string;
  swing_high: number | null;
  swing_high_date: string | null;
  swing_low: number | null;
  swing_low_date: string | null;
  support: number | null;
  resistance: number | null;
  action: string;
  action_reason: string;
}

interface GoldPoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  change_pct: number | null;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma50: number | null;
  ma200: number | null;
  bb_upper: number | null;
  bb_middle: number | null;
  bb_lower: number | null;
  bb_width: number | null;
  adx: number | null;
  plus_di: number | null;
  minus_di: number | null;
}

interface GoldNewsItem {
  title: string;
  snippet: string | null;
  url: string;
  source: string | null;
  published_date: string | null;
}

interface GoldNewsResponse {
  success: boolean;
  items: GoldNewsItem[];
  total: number;
  source: string;
}

interface GoldHistoryResponse {
  symbol: string;
  name: string;
  data: GoldPoint[];
  total: number;
}

/* ------------------------------------------------------------------ */
/*  Time-range selector                                                */
/* ------------------------------------------------------------------ */
type Range = { label: string; days: number };
const RANGES: Range[] = [
  { label: '1月', days: 30 },
  { label: '3月', days: 90 },
  { label: '6月', days: 180 },
  { label: '1年', days: 365 },
  { label: '3年', days: 1095 },
  { label: '全部', days: 5000 },
];

/* ------------------------------------------------------------------ */
/*  Indicator toggle keys                                              */
/* ------------------------------------------------------------------ */
type IndicatorKey = 'ma5' | 'ma10' | 'ma20' | 'ma50' | 'ma200' | 'bb' | 'adx';
const MA_COLORS: Record<string, string> = {
  ma5: '#f97316', ma10: '#eab308', ma20: '#22c55e', ma50: '#3b82f6', ma200: '#ef4444',
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */
const GoldPage: React.FC = () => {
  // ---- data state ----
  const [data, setData] = useState<GoldPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<ParsedApiError | null>(null);
  const [days, setDays] = useState(180);

  // ---- view window (max 6 months visible, drag to pan) ----
  const WINDOW_SIZE = 60;
  const [viewStart, setViewStart] = useState(0);
  const dragState = useRef<{ startX: number; startView: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Reset view to latest when days changes
  useEffect(() => { setViewStart(0); }, [days]);

  // ---- analysis state ----
  const [analyzing, setAnalyzing] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState<string | null>(() => {
    try { return sessionStorage.getItem('gold_ai_analysis'); } catch { return null; }
  });
  const [aiResult, setAiResult] = useState<{ action: string; action_label: string; key_levels: { label: string; price: string; type: string }[]; reasoning: string; risk: string } | null>(() => {
    try { const v = sessionStorage.getItem('gold_ai_result'); return v ? JSON.parse(v) : null; } catch { return null; }
  });
  const [analysisError, setAnalysisError] = useState<ParsedApiError | null>(null);
  const [progressSteps, setProgressSteps] = useState<{ icon: string; label: string; done: boolean }[]>([]);

  // Persist AI results to sessionStorage
  useEffect(() => {
    if (aiAnalysis) {
      try { sessionStorage.setItem('gold_ai_analysis', aiAnalysis); } catch {}
    } else {
      try { sessionStorage.removeItem('gold_ai_analysis'); } catch {}
    }
  }, [aiAnalysis]);
  useEffect(() => {
    if (aiResult) {
      try { sessionStorage.setItem('gold_ai_result', JSON.stringify(aiResult)); } catch {}
    } else {
      try { sessionStorage.removeItem('gold_ai_result'); } catch {}
    }
  }, [aiResult]);

  // ---- indicator toggles ----
  const [toggles, setToggles] = useState<Record<IndicatorKey, boolean>>({
    ma5: true, ma10: true, ma20: true, ma50: false, ma200: false, bb: false, adx: false,
  });

  // ---- technical analysis ----
  const [analysis, setAnalysis] = useState<GoldAnalysis | null>(null);

  const fetchAnalysis = useCallback(async () => {
    try {
      const res = await apiClient.get<GoldAnalysis>('/api/v1/gold/analysis');
      setAnalysis(res.data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchAnalysis(); }, [fetchAnalysis]);

  // ---- gold real-time price ----
  const [goldRt, setGoldRt] = useState<CommodityQuote | null>(null);

  const fetchGoldRt = useCallback(async () => {
    try {
      const res = await apiClient.get<{ success: boolean; data: CommodityQuote[] }>('/api/v1/gold/commodities');
      if (res.data.success) {
        const g = res.data.data.find((c) => c.code === 'GOLD');
        if (g) setGoldRt(g);
      }
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    void fetchGoldRt();
    const t = setInterval(fetchGoldRt, 10000);
    return () => clearInterval(t);
  }, [fetchGoldRt]);

  // ---- gold news ----
  const [newsItems, setNewsItems] = useState<GoldNewsItem[]>([]);
  const [newsLoading, setNewsLoading] = useState(false);

  const fetchNews = useCallback(async () => {
    setNewsLoading(true);
    try {
      const res = await apiClient.get<GoldNewsResponse>('/api/v1/gold/news', { params: { limit: 8 } });
      if (res.data.success) setNewsItems(res.data.items);
    } catch { /* silent */ }
    setNewsLoading(false);
  }, []);

  useEffect(() => { fetchNews(); }, [fetchNews]);

  // ---- fetch gold history ----
  const fetchData = useCallback(async (d: number) => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await apiClient.get<GoldHistoryResponse>('/api/v1/gold/history', { params: { days: d } });
      setData(res.data.data);
    } catch (e) {
      setFetchError(getParsedApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void fetchData(days); }, [days, fetchData]);

  // ---- latest stats ----
  const latest = useMemo(() => data.length > 0 ? data[data.length - 1] : null, [data]);

  // ---- run AI analysis (SSE streaming with progress) ----
  const runAnalysis = useCallback(async () => {
    setAnalyzing(true);
    setAnalysisError(null);
    setAiAnalysis(null);
    setAiResult(null);
    setProgressSteps([]);

    // Abort previous in-flight request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    // Fallback prompt when indicators aren't loaded yet
    const rtPrice = goldRt && goldRt.price > 0 ? goldRt.price : (latest?.close ?? 0);
    const rtChange = goldRt && goldRt.price > 0 ? goldRt.change_pct : (latest?.change_pct ?? 0);
    const hasIndicators = analysis && latest;

    const prompt = hasIndicators ? [
      `XAUUSD 伦敦金技术分析。使用 gold_trend 策略。以下数据已从本地获取，无需再调用工具获取。`,
      ``,
      `【当前盘口】`,
      `现价: ${rtPrice.toFixed(1)} USD/oz | 涨跌: ${rtChange >= 0 ? '+' : ''}${rtChange.toFixed(2)}% | 昨收: ${goldRt && goldRt.prev_close > 0 ? goldRt.prev_close.toFixed(1) : (latest?.open ?? '--')}`,
      ``,
      `【技术指标】`,
      `趋势: ${analysis!.trend}(${analysis!.trend_strength}) | ADX: ${analysis!.adx} | +DI: ${analysis!.plus_di} | -DI: ${analysis!.minus_di}`,
      `均线: ${analysis!.ma_alignment} | 布林: ${analysis!.bb_position}`,
      `波段高: ${analysis!.swing_high?.toFixed(1) ?? '--'} (${analysis!.swing_high_date ?? ''})`,
      `波段低: ${analysis!.swing_low?.toFixed(1) ?? '--'} (${analysis!.swing_low_date ?? ''})`,
      `支撑: ${analysis!.support?.toFixed(1) ?? '--'} | 压力: ${analysis!.resistance?.toFixed(1) ?? '--'}`,
      ``,
      `【最新日K】`,
      `日期: ${latest!.date} | 开: ${latest!.open} | 高: ${latest!.high} | 低: ${latest!.low} | 收: ${latest!.close}`,
      `MA5: ${latest!.ma5?.toFixed(1) ?? '--'} | MA20: ${latest!.ma20?.toFixed(1) ?? '--'} | MA50: ${latest!.ma50?.toFixed(1) ?? '--'}`,
      ``,
      `【黄金交易策略原则 — 与股票不同】`,
      `- 黄金适合趋势跟踪：上升趋势中回调到支撑位加仓，涨到阻力位止盈`,
      `- 不要追高做多：价格远离均线时等回调，不要在高位加仓`,
      `- 跌到支撑位是加仓机会，涨到压力位是止盈时机`,
      ``,
      `在回答末尾输出JSON操作建议:`,
      `{"action":"buy/hold/sell/clear","support_price":数字,"resistance_price":数字,"add_position_price":数字(跌到此价位加仓),"take_profit_price":数字(涨到此价位止盈),"stop_loss":数字,"reasoning":"理由","risk":"风险"}`,
    ].join('\n') : `Please use gold_trend strategy to analyze XAUUSD=X. For gold: buy on dips at support, take profit at resistance. Output JSON with action, support_price, resistance_price, add_position_price, take_profit_price, stop_loss, reasoning, risk.`;

    try {
      const response = await agentApi.chatStream({
        message: prompt,
        skills: ['gold_trend'],
      }, { signal: controller.signal });
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No stream body');

      const decoder = new TextDecoder();
      let buffer = '';
      let fullContent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            switch (evt.type) {
              case 'thinking':
                setProgressSteps((p) => [...p, { icon: '🧠', label: evt.message || `第${evt.step || '?'}步分析中...`, done: false }]);
                break;
              case 'tool_start':
                setProgressSteps((p) => [...p, { icon: '📡', label: `收集: ${evt.display_name || evt.tool || '数据'}`, done: false }]);
                break;
              case 'tool_done':
                setProgressSteps((p) => p.map((s, i) =>
                  i === p.length - 1 ? { ...s, done: true } : s));
                break;
              case 'generating':
                setProgressSteps((p) => [...p, { icon: '✍️', label: evt.message || '生成分析报告...', done: false }]);
                break;
              case 'done':
                fullContent = evt.content || '';
                setProgressSteps((p) => p.map((s) => ({ ...s, done: true })));
                break;
            }
          } catch { /* skip unparseable events */ }
        }
      }

      if (fullContent) {
        setAiAnalysis(fullContent);
        try {
          const jsonMatch = fullContent.match(/\{[\s\S]*"action"[\s\S]*\}/);
          if (jsonMatch) {
            const parsed = JSON.parse(jsonMatch[0]);
            const actionMap: Record<string, { action: string; label: string }> = {
              buy: { action: '加仓', label: '加仓' },
              hold: { action: '观望', label: '观望' },
              sell: { action: '减仓', label: '减仓' },
              clear: { action: '清仓', label: '清仓' },
            };
            const act = actionMap[parsed.action] || { action: '观望', label: '观望' };
            // RMB/g conversion helper
            const toRmb = (usd: number) => {
              const rate = goldRt?.usdcny_rate || 7.25;
              return (usd * rate / 31.1035).toFixed(1);
            };
            const fmtPrice = (v: unknown): string => {
              if (v == null) return '--';
              const n = Number(v);
              if (isNaN(n)) return '--';
              return `${n.toFixed(1)} (¥${toRmb(n)}/g)`;
            };
            setAiResult({
              action: act.action,
              action_label: act.label,
              key_levels: [
                { label: '加仓价', price: fmtPrice(parsed.add_position_price), type: 'buy' },
                { label: '止盈价', price: fmtPrice(parsed.take_profit_price), type: 'sell' },
                { label: '支撑位', price: fmtPrice(parsed.support_price), type: 'support' },
                { label: '阻力位', price: fmtPrice(parsed.resistance_price), type: 'resist' },
                { label: '止损位', price: fmtPrice(parsed.stop_loss), type: 'stop' },
              ],
              reasoning: parsed.reasoning || '',
              risk: parsed.risk || '',
            });
          }
        } catch { /* JSON parse failed */ }
      } else {
        setAiAnalysis('分析完成，但未返回内容');
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return;
      setAnalysisError(getParsedApiError(e));
    } finally {
      setAnalyzing(false);
    }
  }, [analysis, latest, goldRt]);

  // Clear progress steps a moment after analysis completes
  useEffect(() => {
    if (!analyzing && progressSteps.length > 0 && progressSteps.every((s) => s.done)) {
      const t = setTimeout(() => setProgressSteps([]), 2000);
      return () => clearTimeout(t);
    }
  }, [analyzing, progressSteps]);

  // ---- chart data prep (with downsampling for large datasets) ----
  const MAX_CHART_POINTS = 250;
  const chartData = useMemo(() => {
    const mapped = data.map((d, i) => ({
      ...d,
      _idx: i,
      dateLabel: d.date.slice(5), // MM-DD
      volumeK: d.volume ? Math.round(d.volume / 1000) : 0,
    }));
    if (mapped.length <= MAX_CHART_POINTS) return mapped;
    // Evenly sample to ~MAX_CHART_POINTS, always include last point
    const step = mapped.length / MAX_CHART_POINTS;
    const sampled: typeof mapped = [];
    for (let i = 0; i < mapped.length - 1; i += step) {
      sampled.push(mapped[Math.floor(i)]);
    }
    sampled.push(mapped[mapped.length - 1]);
    return sampled;
  }, [data]);

  // ---- visible window ----
  const visibleData = useMemo(() => {
    if (chartData.length <= WINDOW_SIZE) return chartData;
    const end = chartData.length - viewStart;
    const start = Math.max(0, end - WINDOW_SIZE);
    return chartData.slice(start, end);
  }, [chartData, viewStart]);

  const maxViewStart = Math.max(0, chartData.length - WINDOW_SIZE);

  // ---- drag handlers ----
  const handleChartMouseDown = useCallback((e: React.MouseEvent) => {
    dragState.current = { startX: e.clientX, startView: viewStart };
  }, [viewStart]);

  const handleChartMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragState.current || chartData.length <= WINDOW_SIZE) return;
    const dx = dragState.current.startX - e.clientX;
    const pointsPerPx = chartData.length / (e.currentTarget as HTMLElement).offsetWidth;
    const offset = Math.round(dx * pointsPerPx);
    const newStart = Math.max(0, Math.min(maxViewStart, dragState.current.startView + offset));
    setViewStart(newStart);
  }, [chartData.length, maxViewStart]);

  const handleChartMouseUp = useCallback(() => {
    dragState.current = null;
  }, []);

  useEffect(() => {
    const handleUp = () => { dragState.current = null; };
    window.addEventListener('mouseup', handleUp);
    return () => window.removeEventListener('mouseup', handleUp);
  }, []);

  // ---- price domain (from full data for stable Y axis) ----
  const priceDomain = useMemo(() => {
    if (data.length === 0) return [0, 100];
    const highs = data.map((d) => d.bb_upper ?? d.high).filter((v) => v != null) as number[];
    const lows = data.map((d) => d.bb_lower ?? d.low).filter((v) => v != null) as number[];
    if (highs.length === 0) return [0, 100];
    const pad = (Math.max(...highs) - Math.min(...lows)) * 0.08;
    return [Math.floor(Math.min(...lows) - pad), Math.ceil(Math.max(...highs) + pad)];
  }, [data]);

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */
  return (
    <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-5 px-4 py-6 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-5">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-amber-500/20 text-amber-500">
            <CircleDollarSign className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-foreground">伦敦金 XAUUSD</h1>
            <p className="text-xs text-secondary-text">
              {goldRt && goldRt.price > 0 ? '实时' : latest ? '最新' : '加载中...'}
              {' | 日线 | '}{data.length}条
            </p>
          </div>
        </div>

        {/* Price display */}
        {(() => {
          const rtPrice = goldRt && goldRt.price > 0 ? goldRt.price : null;
          const rtChange = goldRt && goldRt.price > 0 ? goldRt.change : null;
          const rtPct = goldRt && goldRt.price > 0 ? goldRt.change_pct : null;
          const rmbGram = goldRt?.rmb_per_gram;
          const displayPrice = rtPrice ?? (latest ? latest.close : null);
          const displayChange = rtChange ?? (latest ? latest.change_pct : null);
          const isUp = displayChange != null ? displayChange >= 0 : true;
          const upColor = 'text-red-500';
          const downColor = 'text-green-500';

          return (
            <div className="flex items-baseline gap-x-4 gap-y-1 flex-wrap">
              <div className="flex items-baseline gap-2">
                <span className={`text-2xl font-bold tabular-nums ${
                  displayPrice != null ? isUp ? upColor : downColor : 'text-foreground'
                }`}>
                  {displayPrice != null ? displayPrice.toFixed(1) : '--'}
                </span>
                <span className="text-xs text-secondary-text">USD/oz</span>
              </div>
              {rmbGram != null && (
                <div className="flex items-baseline gap-2">
                  <span className={`text-xl font-semibold tabular-nums ${isUp ? upColor : downColor}`}>
                    ¥{rmbGram.toFixed(1)}
                  </span>
                  <span className="text-xs text-secondary-text">RMB/g</span>
                </div>
              )}
              {rtPrice != null && (
                <div className="flex items-baseline gap-1.5">
                  <span className={`text-sm font-medium tabular-nums ${isUp ? upColor : downColor}`}>
                    {rtChange! >= 0 ? '+' : ''}{rtChange!.toFixed(1)}
                  </span>
                  <span className={`text-sm font-medium tabular-nums ${isUp ? upColor : downColor}`}>
                    ({rtPct! >= 0 ? '+' : ''}{rtPct!.toFixed(2)}%)
                  </span>
                </div>
              )}
            </div>
          );
        })()}

        <Button onClick={runAnalysis} disabled={analyzing} className="gap-2">
          <RefreshCw className={`h-4 w-4 ${analyzing ? 'animate-spin' : ''}`} />
          {analyzing ? '分析中...' : 'AI 趋势分析'}
        </Button>
      </div>

      {/* Stats bar */}
      {latest && (
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
          {[
            ['MA20', latest.ma20, latest.close > (latest.ma20 ?? 0) ? 'text-red-500' : 'text-green-500'],
            ['MA50', latest.ma50],
            ['MA200', latest.ma200],
            ['ADX', latest.adx],
            ['BB Upper', latest.bb_upper],
            ['BB Lower', latest.bb_lower],
          ].map(([label, val, cls]) => (
            <Card key={label as string} className="px-3 py-2 text-center">
              <div className="text-[10px] text-secondary-text">{label}</div>
              <div className={`text-sm font-semibold ${cls || 'text-foreground'}`}>
                {val != null ? Number(val).toFixed(1) : '--'}
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Technical analysis card */}
      {analysis && (
        <Card className="p-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Trend & action */}
            <div className="flex flex-col gap-2">
              <div className="label-uppercase text-[10px] text-secondary-text">趋势判断</div>
              <div className="flex items-center gap-2">
                {analysis.trend === '上升趋势' ? <TrendingUp className="h-5 w-5 text-red-500" />
                  : analysis.trend === '下降趋势' ? <TrendingDown className="h-5 w-5 text-green-500" />
                  : <Minus className="h-5 w-5 text-amber-500" />}
                <span className={`text-lg font-semibold ${
                  analysis.trend === '上升趋势' ? 'text-red-500' :
                  analysis.trend === '下降趋势' ? 'text-green-500' : 'text-amber-500'
                }`}>{analysis.trend}</span>
                <span className="text-xs text-secondary-text">({analysis.trend_strength})</span>
              </div>
              <div className={`mt-2 px-3 py-1.5 rounded-lg text-sm font-semibold inline-flex items-center gap-1.5 w-fit ${
                analysis.action === '加仓' ? 'bg-red-500/10 text-red-500' :
                analysis.action === '减仓' ? 'bg-green-500/10 text-green-500' :
                'bg-amber-500/10 text-amber-500'
              }`}>
                {analysis.action === '加仓' ? <TrendingUp className="h-4 w-4" /> :
                 analysis.action === '减仓' ? <TrendingDown className="h-4 w-4" /> :
                 <Minus className="h-4 w-4" />}
                {analysis.action}
              </div>
              <p className="text-xs text-secondary-text leading-relaxed">{analysis.action_reason}</p>
            </div>

            {/* Key levels */}
            <div className="flex flex-col gap-2">
              <div className="label-uppercase text-[10px] text-secondary-text">关键价位</div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg bg-red-500/5 px-2 py-1.5">
                  <div className="text-secondary-text">压力位</div>
                  <div className="font-semibold text-red-500">
                    {analysis.resistance != null ? analysis.resistance.toFixed(1) : '--'}
                  </div>
                </div>
                <div className="rounded-lg bg-green-500/5 px-2 py-1.5">
                  <div className="text-secondary-text">支撑位</div>
                  <div className="font-semibold text-green-500">
                    {analysis.support != null ? analysis.support.toFixed(1) : '--'}
                  </div>
                </div>
                <div className="rounded-lg bg-amber-500/5 px-2 py-1.5">
                  <div className="text-secondary-text">波段高</div>
                  <div className="font-semibold text-foreground">
                    {analysis.swing_high != null ? analysis.swing_high.toFixed(1) : '--'}
                  </div>
                  {analysis.swing_high_date && (
                    <div className="text-[9px] text-secondary-text">{analysis.swing_high_date}</div>
                  )}
                </div>
                <div className="rounded-lg bg-amber-500/5 px-2 py-1.5">
                  <div className="text-secondary-text">波段低</div>
                  <div className="font-semibold text-foreground">
                    {analysis.swing_low != null ? analysis.swing_low.toFixed(1) : '--'}
                  </div>
                  {analysis.swing_low_date && (
                    <div className="text-[9px] text-secondary-text">{analysis.swing_low_date}</div>
                  )}
                </div>
              </div>
            </div>

            {/* Indicators */}
            <div className="flex flex-col gap-2">
              <div className="label-uppercase text-[10px] text-secondary-text">技术指标</div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-secondary-text">ADX</span>
                  <span className={`font-medium ${analysis.adx >= 25 ? 'text-red-500' : analysis.adx >= 20 ? 'text-amber-500' : 'text-secondary-text'}`}>
                    {analysis.adx.toFixed(1)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-secondary-text">+DI</span>
                  <span className="font-medium text-red-500">{analysis.plus_di.toFixed(1)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-secondary-text">-DI</span>
                  <span className="font-medium text-green-500">{analysis.minus_di.toFixed(1)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-secondary-text">布林</span>
                  <span className="font-medium text-foreground">{analysis.bb_position}</span>
                </div>
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-xs">
                <span className="text-secondary-text">均线</span>
                <span className={`font-medium px-1.5 py-0.5 rounded ${
                  analysis.ma_alignment === '多头排列' ? 'bg-red-500/10 text-red-500' :
                  analysis.ma_alignment === '空头排列' ? 'bg-green-500/10 text-green-500' :
                  'bg-amber-500/10 text-amber-500'
                }`}>{analysis.ma_alignment}</span>
              </div>
            </div>

            {/* Summary */}
            <div className="flex flex-col gap-2">
              <div className="label-uppercase text-[10px] text-secondary-text">当前价格</div>
              <div className="text-2xl font-bold tabular-nums text-foreground">
                {analysis.current_price.toFixed(1)}
                <span className="text-xs text-secondary-text ml-1">USD/oz</span>
              </div>
              {goldRt?.rmb_per_gram != null && (
                <div className="text-lg font-semibold tabular-nums text-foreground">
                  ¥{goldRt.rmb_per_gram.toFixed(1)}
                  <span className="text-xs text-secondary-text ml-1">RMB/g</span>
                </div>
              )}
              <div className="flex flex-wrap gap-1.5 mt-1">
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                  analysis.trend !== '震荡' ? 'bg-primary/10 text-primary' : 'bg-amber-500/10 text-amber-500'
                }`}>
                  {analysis.trend !== '震荡' ? '趋势明确' : '方向不明'}
                </span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                  analysis.action === '加仓' ? 'bg-red-500/10 text-red-500' :
                  analysis.action === '减仓' ? 'bg-green-500/10 text-green-500' :
                  'bg-amber-500/10 text-amber-500'
                }`}>
                  建议{analysis.action}
                </span>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Time range + indicator toggles */}
      <div className="flex flex-wrap items-center gap-2">
        {RANGES.map((r) => (
          <button
            key={r.label}
            type="button"
            onClick={() => setDays(r.days)}
            className={`rounded-lg px-3 py-1 text-xs transition-colors ${
              days === r.days
                ? 'bg-primary/15 text-primary font-medium'
                : 'text-secondary-text hover:text-foreground'
            }`}
          >
            {r.label}
          </button>
        ))}
        <span className="mx-2 w-px h-4 bg-border/60" />
        {(Object.keys(MA_COLORS) as IndicatorKey[]).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setToggles((t) => ({ ...t, [k]: !t[k] }))}
            className={`rounded-lg px-2 py-1 text-[11px] transition-colors ${
              toggles[k] ? 'font-medium' : 'opacity-40'
            }`}
            style={{ color: toggles[k] ? MA_COLORS[k] : undefined }}
          >
            {k.toUpperCase()}
          </button>
        ))}
        <span className="mx-1 w-px h-4 bg-border/60" />
        {(['bb', 'adx'] as IndicatorKey[]).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setToggles((t) => ({ ...t, [k]: !t[k] }))}
            className={`rounded-lg px-2 py-1 text-[11px] transition-colors ${
              toggles[k] ? 'bg-primary/10 text-primary font-medium' : 'opacity-40 text-secondary-text'
            }`}
          >
            {k === 'bb' ? 'BOLL' : 'ADX'}
          </button>
        ))}
      </div>

      {/* Errors */}
      {fetchError && <ApiErrorAlert error={fetchError} />}

      {/* Chart */}
      {loading ? (
        <Card className="flex items-center justify-center py-24"><Loading /></Card>
      ) : chartData.length === 0 ? (
        <Card className="flex items-center justify-center py-16 text-secondary-text text-sm">
          暂无数据
        </Card>
      ) : (
        <>
          {/* K-line + MA + BB chart */}
          <div
            onMouseDown={handleChartMouseDown}
            onMouseMove={handleChartMouseMove}
            onMouseUp={handleChartMouseUp}
            onMouseLeave={handleChartMouseUp}
            style={{ cursor: chartData.length > WINDOW_SIZE ? 'grab' : 'default' }}
          >
            <Card className="p-0 overflow-hidden">
              <div className="px-4 pt-4 pb-0 flex items-center justify-between">
                <p className="text-sm font-medium text-foreground">K线 & 均线</p>
                {chartData.length > WINDOW_SIZE && (
                  <p className="text-[10px] text-secondary-text">
                    {visibleData[0]?.date ?? ''} — {visibleData[visibleData.length - 1]?.date ?? ''} | 拖拽平移
                  </p>
                )}
              </div>
              <ResponsiveContainer width="100%" height={420}>
                <ComposedChart data={visibleData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                  <XAxis dataKey="dateLabel" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                  <YAxis
                    domain={priceDomain}
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) => v.toFixed(0)}
                    width={60}
                    orientation="right"
                  />
                  <Tooltip
                    content={({ active, label }) => {
                      if (!active || !label) return null;
                      const pt = visibleData.find((d) => d.dateLabel === label);
                      if (!pt) return null;
                      const up = pt.close >= pt.open;
                      const color = up ? '#ef4444' : '#22c55e';
                      return (
                        <div style={{
                          background: '#fff', border: '1px solid #d1d5db',
                          borderRadius: 12, fontSize: 12, color: '#1f2937',
                          padding: '8px 12px', minWidth: 150,
                        }}>
                          <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 6 }}>
                            日期: {pt.date}
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '2px 8px' }}>
                            <span style={{ color: '#6b7280' }}>开盘</span>
                            <span style={{ fontWeight: 600, textAlign: 'right' }}>{pt.open.toFixed(1)}</span>
                            <span style={{ color: '#6b7280' }}>最高</span>
                            <span style={{ fontWeight: 600, textAlign: 'right', color: '#ef4444' }}>{pt.high.toFixed(1)}</span>
                            <span style={{ color: '#6b7280' }}>最低</span>
                            <span style={{ fontWeight: 600, textAlign: 'right', color: '#22c55e' }}>{pt.low.toFixed(1)}</span>
                            <span style={{ color: '#6b7280' }}>收盘</span>
                            <span style={{ fontWeight: 700, textAlign: 'right', color }}>{pt.close.toFixed(1)}</span>
                          </div>
                          <div style={{ marginTop: 6, paddingTop: 5, borderTop: '1px solid #e5e7eb', display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                            <span style={{ color: '#6b7280' }}>涨跌</span>
                            <span style={{ fontWeight: 600, color: pt.change_pct != null ? (pt.change_pct >= 0 ? '#ef4444' : '#22c55e') : '#6b7280' }}>
                              {pt.change_pct != null ? `${pt.change_pct >= 0 ? '+' : ''}${pt.change_pct.toFixed(2)}%` : '--'}
                            </span>
                          </div>
                          {pt.volume != null && pt.volume > 0 && (
                            <div style={{ marginTop: 2, display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                              <span style={{ color: '#6b7280' }}>量</span>
                              <span style={{ color: '#374151' }}>{pt.volume.toLocaleString()}</span>
                            </div>
                          )}
                        </div>
                      );
                    }}
                  />

                  {/* Close price area for tooltip tracking */}
                  <Area
                    type="monotone"
                    dataKey="close"
                    stroke="#f59e0b"
                    strokeWidth={1}
                    fill="none"
                    name="收盘价"
                  />

                  {/* K-line candlestick bodies — wick + open/close rect */}
                  <Bar
                    dataKey="close"
                    isAnimationActive={false}
                    shape={(props: any) => {
                      const { x, y, width, height, payload } = props;
                      if (!payload || !height || height <= 0) return null;
                      const { open, high, low } = payload;
                      if (high == null || low == null || open == null) return null;
                      const domMin = priceDomain[0];
                      const dataRange = payload.close - domMin;
                      if (dataRange <= 0) return null;
                      const pxPerUnit = height / dataRange;
                      const baselineY = y + height;
                      const toY = (v: number) => baselineY - (v - domMin) * pxPerUnit;

                      const hiY = toY(high);
                      const loY = toY(low);
                      const opY = toY(open);
                      const clY = y;

                      const up = payload.close >= open;
                      const color = up ? '#ef4444' : '#22c55e';
                      const bodyW = Math.max(1.5, Math.min(5, width * 0.55));
                      const cx = x + width / 2;
                      const bodyTop = Math.min(opY, clY);
                      const bodyH = Math.max(Math.abs(clY - opY), 0.5);
                      return (
                        <g>
                          <line x1={cx} y1={hiY} x2={cx} y2={loY} stroke={color} strokeWidth={0.5} />
                          <rect
                            x={cx - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH}
                            fill={up ? color : 'transparent'} stroke={color} strokeWidth={0.5}
                          />
                        </g>
                      );
                    }}
                  />

                  {/* MA lines */}
                  {toggles.ma5 && <Line type="monotone" dataKey="ma5" stroke={MA_COLORS.ma5} strokeWidth={1} dot={false} name="MA5" />}
                  {toggles.ma10 && <Line type="monotone" dataKey="ma10" stroke={MA_COLORS.ma10} strokeWidth={1} dot={false} name="MA10" />}
                  {toggles.ma20 && <Line type="monotone" dataKey="ma20" stroke={MA_COLORS.ma20} strokeWidth={1.5} dot={false} name="MA20" />}
                  {toggles.ma50 && <Line type="monotone" dataKey="ma50" stroke={MA_COLORS.ma50} strokeWidth={1.5} dot={false} name="MA50" />}
                  {toggles.ma200 && <Line type="monotone" dataKey="ma200" stroke={MA_COLORS.ma200} strokeWidth={1.5} dot={false} name="MA200" />}

                  {/* BB lines */}
                  {toggles.bb && <Line type="monotone" dataKey="bb_upper" stroke="#6366f1" strokeWidth={1} strokeDasharray="4 2" dot={false} name="BB上轨" />}
                  {toggles.bb && <Line type="monotone" dataKey="bb_middle" stroke="#6366f1" strokeWidth={0.5} dot={false} name="BB中轨" />}
                  {toggles.bb && <Line type="monotone" dataKey="bb_lower" stroke="#6366f1" strokeWidth={1} strokeDasharray="4 2" dot={false} name="BB下轨" />}
                </ComposedChart>
              </ResponsiveContainer>
            </Card>
          </div>


          {/* ADX sub-chart */}
          {toggles.adx && (
            <Card className="p-0 overflow-hidden">
              <div className="px-4 pt-3 pb-0">
                <p className="text-xs text-secondary-text">ADX (14)</p>
              </div>
              <ResponsiveContainer width="100%" height={120}>
                <ComposedChart data={visibleData} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                  <XAxis dataKey="dateLabel" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 10 }} domain={[0, 'auto']} width={40} orientation="right" />
                  <Tooltip
                    contentStyle={{ background: '#fff', border: '1px solid #d1d5db', borderRadius: 12, fontSize: 12, color: '#1f2937' }}
                  />
                  <Line type="monotone" dataKey="adx" stroke="#a855f7" strokeWidth={1.5} dot={false} name="ADX" />
                  <Line type="monotone" dataKey="plus_di" stroke="#22c55e" strokeWidth={1} dot={false} name="+DI" />
                  <Line type="monotone" dataKey="minus_di" stroke="#ef4444" strokeWidth={1} dot={false} name="-DI" />
                  <Line type="monotone" dataKey={() => 25} stroke="#9ca3af" strokeWidth={1} strokeDasharray="3 3" dot={false} name="ADX=25" />
                </ComposedChart>
              </ResponsiveContainer>
            </Card>
          )}

          {/* Timeline scrollbar */}
          {chartData.length > WINDOW_SIZE && (
            <div className="flex items-center gap-2 px-4">
              <span className="text-[10px] text-secondary-text whitespace-nowrap">
                {chartData[0]?.date?.slice(0, 7) ?? ''}
              </span>
              <div
                className="relative flex-1 h-5 flex items-center cursor-pointer"
                onMouseDown={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                  setViewStart(Math.round(ratio * maxViewStart));
                  dragState.current = { startX: e.clientX, startView: viewStart };
                }}
                onMouseMove={(e) => {
                  if (!dragState.current || chartData.length <= WINDOW_SIZE) return;
                  const rect = e.currentTarget.getBoundingClientRect();
                  const dx = dragState.current.startX - e.clientX;
                  const ratio = dx / rect.width;
                  const offset = Math.round(ratio * maxViewStart);
                  const newStart = Math.max(0, Math.min(maxViewStart, dragState.current.startView + offset));
                  setViewStart(newStart);
                }}
              >
                <div className="absolute inset-y-0 left-0 right-0 rounded bg-border/30" />
                <div
                  className="absolute inset-y-0 rounded bg-primary/30 border border-primary/50"
                  style={{
                    left: `${((maxViewStart - viewStart) / maxViewStart) * 100}%`,
                    width: `${(WINDOW_SIZE / chartData.length) * 100}%`,
                  }}
                />
              </div>
              <span className="text-[10px] text-secondary-text whitespace-nowrap">
                {chartData[chartData.length - 1]?.date?.slice(0, 7) ?? ''}
              </span>
            </div>
          )}
        </>
      )}

      {/* AI Analysis result */}
      {analysisError && <ApiErrorAlert error={analysisError} />}

      {/* Progress steps during AI analysis */}
      {progressSteps.length > 0 && (
        <Card className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <RefreshCw className="h-4 w-4 animate-spin text-primary" />
            <span className="text-sm font-medium text-foreground">AI 分析进行中...</span>
          </div>
          <div className="flex flex-col gap-1.5">
            {progressSteps.map((step, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="w-5 text-center">{step.done ? '✅' : step.icon}</span>
                <span className={step.done ? 'text-secondary-text line-through' : 'text-foreground'}>
                  {step.label}
                </span>
                {!step.done && <span className="ml-auto inline-block w-3 h-3 border-2 border-primary border-t-transparent rounded-full animate-spin" />}
              </div>
            ))}
          </div>
        </Card>
      )}

      {aiResult && (
        <Card className="p-5 border-l-4 border-l-primary">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Action recommendation */}
            <div className="flex flex-col gap-2">
              <div className="label-uppercase text-[10px] text-secondary-text flex items-center gap-1">
                <Bot className="h-3 w-3" /> AI 操作建议
              </div>
              <div className={`px-3 py-2 rounded-lg text-lg font-bold inline-flex items-center gap-2 w-fit ${
                aiResult.action === '加仓' ? 'bg-red-500/10 text-red-500' :
                aiResult.action === '减仓' ? 'bg-green-500/10 text-green-500' :
                aiResult.action === '清仓' ? 'bg-red-500/20 text-red-600' :
                'bg-amber-500/10 text-amber-500'
              }`}>
                {aiResult.action === '加仓' ? <TrendingUp className="h-5 w-5" /> :
                 aiResult.action === '减仓' ? <TrendingDown className="h-5 w-5" /> :
                 aiResult.action === '清仓' ? <TrendingDown className="h-5 w-5" /> :
                 <Minus className="h-5 w-5" />}
                {aiResult.action_label}
              </div>
              {aiResult.reasoning && (
                <p className="text-xs text-secondary-text leading-relaxed">{aiResult.reasoning}</p>
              )}
            </div>

            {/* Key price levels */}
            <div className="flex flex-col gap-2">
              <div className="label-uppercase text-[10px] text-secondary-text">AI 关键价位</div>
              <div className="grid grid-cols-2 gap-1.5 text-xs">
                {aiResult.key_levels.map((lv) => (
                  <div key={lv.label} className={`rounded-lg px-2 py-1.5 ${
                    lv.type === 'buy' ? 'bg-red-500/5' :
                    lv.type === 'sell' ? 'bg-green-500/5' :
                    lv.type === 'stop' ? 'bg-red-500/10' :
                    'bg-amber-500/5'
                  }`}>
                    <div className="text-secondary-text">{lv.label}</div>
                    <div className={`font-semibold ${
                      lv.type === 'buy' || lv.type === 'stop' ? 'text-red-500' :
                      lv.type === 'sell' ? 'text-green-500' : 'text-foreground'
                    }`}>{lv.price}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Risk */}
            <div className="flex flex-col gap-2">
              <div className="label-uppercase text-[10px] text-secondary-text">风险提示</div>
              <div className="rounded-lg bg-amber-500/5 px-3 py-2 text-xs text-secondary-text leading-relaxed flex-1">
                {aiResult.risk || 'AI 未返回风险提示'}
              </div>
            </div>

            {/* Raw AI text (collapsed preview) */}
            <div className="flex flex-col gap-2">
              <div className="label-uppercase text-[10px] text-secondary-text">AI 分析原文</div>
              <div className="rounded-lg bg-foreground/5 px-3 py-2 text-xs text-secondary-text leading-relaxed flex-1 max-h-32 overflow-y-auto whitespace-pre-wrap">
                {aiAnalysis || '--'}
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Fallback: raw AI text when JSON parse failed */}
      {!aiResult && aiAnalysis && (
        <Card className="p-5">
          <div className="prose prose-sm max-w-none dark:prose-invert whitespace-pre-wrap text-sm leading-relaxed">
            {aiAnalysis}
          </div>
        </Card>
      )}

      {/* Gold news */}
      <Card className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <svg className="h-4 w-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z" />
            </svg>
            <span className="text-sm font-medium text-foreground">相关资讯</span>
            {newsItems.length > 0 && (
              <span className="text-[10px] text-secondary-text">({newsItems.length}条)</span>
            )}
          </div>
          <button
            type="button"
            onClick={fetchNews}
            className={`text-[10px] text-primary hover:text-primary/80 transition-colors ${newsLoading ? 'pointer-events-none opacity-50' : ''}`}
          >
            {newsLoading ? '刷新中...' : '刷新'}
          </button>
        </div>
        {newsLoading && newsItems.length === 0 ? (
          <div className="flex justify-center py-6"><Loading /></div>
        ) : newsItems.length === 0 ? (
          <p className="text-xs text-secondary-text text-center py-6">
            暂无新闻数据。执行一次 AI 分析后，资讯将自动缓存在本地。
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {newsItems.slice(0, 8).map((item, i) => (
              <a
                key={`${item.url}-${i}`}
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block rounded-lg px-3 py-2 hover:bg-foreground/5 transition-colors group"
              >
                <div className="text-xs font-medium text-foreground leading-snug line-clamp-2 group-hover:text-primary transition-colors">
                  {item.title}
                </div>
                {item.snippet && (
                  <div className="mt-1 text-[10px] text-secondary-text leading-relaxed line-clamp-2">
                    {item.snippet}
                  </div>
                )}
                <div className="mt-1.5 flex items-center gap-2 text-[9px] text-secondary-text">
                  {item.source && <span>{item.source}</span>}
                  {item.published_date && <span>{item.published_date}</span>}
                </div>
              </a>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};

export default GoldPage;
