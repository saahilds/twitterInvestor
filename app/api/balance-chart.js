/**
 * Robinhood-style account balance chart (TradingView Lightweight Charts).
 */
(function (global) {
  const RANGE_LABELS = {
    "1d": "Today",
    "1w": "Past week",
    "1m": "Past month",
    "3m": "Past 3 months",
    ytd: "Year to date",
    all: "All time",
  };

  const COLORS = {
    up: "#34d399",
    upFill: "rgba(52, 211, 153, 0.18)",
    down: "#f87171",
    downFill: "rgba(248, 113, 113, 0.18)",
    grid: "#2a3545",
    text: "#8b9cb3",
    crosshair: "#8b9cb3",
  };

  function fmtUsd(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return `$${Number(n).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function fmtSignedUsd(n) {
    if (n == null || Number.isNaN(n)) return "—";
    const sign = n >= 0 ? "+" : "−";
    return `${sign}$${Math.abs(n).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function fmtPct(n) {
    if (n == null || Number.isNaN(n)) return "0.00%";
    const sign = n >= 0 ? "+" : "−";
    return `${sign}${Math.abs(n).toFixed(2)}%`;
  }

  function fmtCrosshairTime(isoOrSec, rangeKey) {
    try {
      const d = typeof isoOrSec === "number" ? new Date(isoOrSec * 1000) : new Date(isoOrSec);
      if (rangeKey === "1d") {
        return d.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        });
      }
      return d.toLocaleDateString(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    } catch {
      return String(isoOrSec);
    }
  }

  function toEtDateString(iso) {
    const d = new Date(iso);
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(d);
    const y = parts.find((p) => p.type === "year").value;
    const m = parts.find((p) => p.type === "month").value;
    const day = parts.find((p) => p.type === "day").value;
    return `${y}-${m}-${day}`;
  }

  function toSeriesPoints(apiPoints, rangeKey) {
    const intraday = rangeKey === "1d";
    const mapped = apiPoints
      .map((p) => {
        const value = Number(p.v);
        if (intraday) {
          return { time: Math.floor(new Date(p.t).getTime() / 1000), value };
        }
        return { time: toEtDateString(p.t), value };
      })
      .sort((a, b) => {
        const ta = typeof a.time === "number" ? a.time : new Date(a.time).getTime() / 1000;
        const tb = typeof b.time === "number" ? b.time : new Date(b.time).getTime() / 1000;
        return ta - tb;
      });

    if (mapped.length === 1) {
      const only = mapped[0];
      if (typeof only.time === "number") {
        mapped.unshift({ time: only.time - 3600, value: only.value });
      } else {
        const prev = new Date(only.time);
        prev.setDate(prev.getDate() - 1);
        mapped.unshift({
          time: prev.toISOString().slice(0, 10),
          value: only.value,
        });
      }
    }
    return mapped;
  }

  function toMarkerTime(iso, rangeKey) {
    if (rangeKey === "1d") {
      return Math.floor(new Date(iso).getTime() / 1000);
    }
    return toEtDateString(iso);
  }

  function snapMarkerTime(markerTime, seriesData, rangeKey) {
    if (rangeKey === "1d" || !seriesData.length) {
      return markerTime;
    }
    if (typeof markerTime !== "string") {
      return markerTime;
    }
    const exact = seriesData.find((p) => p.time === markerTime);
    if (exact) return markerTime;

    const target = new Date(markerTime).getTime() / 1000;
    let best = seriesData[0].time;
    let bestDist = Infinity;
    for (const point of seriesData) {
      const t = new Date(point.time).getTime() / 1000;
      const dist = Math.abs(t - target);
      if (dist < bestDist) {
        bestDist = dist;
        best = point.time;
      }
    }
    return best;
  }

  function buildMarkers(annotations, rangeKey, seriesData) {
    return (annotations || []).map((trade) => {
      const isBuy = trade.action === "BUY";
      const time = snapMarkerTime(toMarkerTime(trade.t, rangeKey), seriesData, rangeKey);
      return {
        time,
        position: isBuy ? "belowBar" : "aboveBar",
        color: isBuy ? COLORS.up : COLORS.down,
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: trade.ticker,
      };
    });
  }

  function visibleRangeFromWindow(payload, rangeKey) {
    const startIso = payload.window_start || payload.session_open;
    const endIso = payload.window_end || payload.session_end;
    if (!startIso || !endIso) return null;
    if (rangeKey === "1d") {
      return {
        from: Math.floor(new Date(startIso).getTime() / 1000),
        to: Math.floor(new Date(endIso).getTime() / 1000),
      };
    }
    return {
      from: toEtDateString(startIso),
      to: toEtDateString(endIso),
    };
  }

  class BalanceChart {
    constructor(rootEl, options = {}) {
      if (!rootEl) throw new Error("BalanceChart: root element required");
      this.root = rootEl;
      this.onRangeChange = options.onRangeChange || (() => {});
      this.rangeKey = "1d";
      this.payload = null;
      this.scrubbing = false;
      this.heroValue = null;
      this.heroChange = null;
      this.heroPeriod = null;
      this.heroScrub = null;
      this.metaEl = null;
      this.chartEl = null;
      this.overlayEl = null;
      this.chart = null;
      this.series = null;
      this.seriesMarkers = null;
      this.tradeAnnotations = [];
      this.resizeObserver = null;
      this._visibleRangeHandler = null;
      this._buildDom();
      this._initChart();
    }

    _buildDom() {
      this.root.innerHTML = `
        <div class="balance-hero">
          <div class="balance-hero-value" data-hero-value>—</div>
          <div class="balance-hero-row">
            <span class="balance-hero-change" data-hero-change>—</span>
            <span class="balance-hero-period" data-hero-period></span>
          </div>
          <div class="balance-hero-scrub" data-hero-scrub hidden></div>
        </div>
        <div class="balance-legend">
          <span class="buy">Buy</span>
          <span class="sell">Sell</span>
        </div>
        <div class="balance-chart-canvas" data-chart-canvas>
          <div class="balance-lwc-mount" data-lwc-mount></div>
          <div class="balance-trade-overlay" data-trade-overlay></div>
        </div>
        <div class="balance-chart-meta" data-chart-meta></div>
        <div class="range-group balance-range-group" data-range-group>
          <button type="button" class="range-btn" data-range="1d">1D</button>
          <button type="button" class="range-btn" data-range="1w">1W</button>
          <button type="button" class="range-btn" data-range="1m">1M</button>
          <button type="button" class="range-btn" data-range="3m">3M</button>
          <button type="button" class="range-btn" data-range="ytd">YTD</button>
          <button type="button" class="range-btn" data-range="all">All</button>
        </div>
      `;
      this.heroValue = this.root.querySelector("[data-hero-value]");
      this.heroChange = this.root.querySelector("[data-hero-change]");
      this.heroPeriod = this.root.querySelector("[data-hero-period]");
      this.heroScrub = this.root.querySelector("[data-hero-scrub]");
      this.metaEl = this.root.querySelector("[data-chart-meta]");
      this.chartEl = this.root.querySelector("[data-chart-canvas]");
      this.lwcMount = this.root.querySelector("[data-lwc-mount]");
      this.overlayEl = this.root.querySelector("[data-trade-overlay]");
      const rangeGroup = this.root.querySelector("[data-range-group]");
      rangeGroup.addEventListener("click", (e) => {
        const btn = e.target.closest(".range-btn");
        if (!btn) return;
        this.setRange(btn.dataset.range);
        this.onRangeChange(btn.dataset.range);
      });
    }

    _initChart() {
      const LWC = global.LightweightCharts;
      if (!LWC) {
        this.chartEl.textContent = "Chart library failed to load.";
        return;
      }
      const mount = this.lwcMount || this.chartEl;
      const width = this.chartEl.clientWidth || 600;
      this.chart = LWC.createChart(mount, {
        width,
        height: 300,
        layout: {
          background: { color: "transparent" },
          textColor: COLORS.text,
          fontFamily: "system-ui, sans-serif",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: COLORS.grid, visible: false },
          horzLines: { color: COLORS.grid, visible: true },
        },
        rightPriceScale: { visible: false },
        leftPriceScale: { visible: false },
        timeScale: {
          borderColor: COLORS.grid,
          timeVisible: true,
          secondsVisible: false,
          fixLeftEdge: true,
          fixRightEdge: true,
        },
        crosshair: {
          mode: LWC.CrosshairMode ? LWC.CrosshairMode.Magnet : 1,
          vertLine: { color: COLORS.crosshair, width: 1, style: 2 },
          horzLine: { visible: false },
        },
        handleScroll: false,
        handleScale: false,
      });

      const seriesOptions = {
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 4,
      };
      if (typeof this.chart.addAreaSeries === "function") {
        this.series = this.chart.addAreaSeries(seriesOptions);
      } else if (typeof this.chart.addSeries === "function" && LWC.AreaSeries) {
        this.series = this.chart.addSeries(LWC.AreaSeries, seriesOptions);
      } else {
        this.series = this.chart.addLineSeries(seriesOptions);
      }

      if (typeof LWC.createSeriesMarkers === "function") {
        this.seriesMarkers = LWC.createSeriesMarkers(this.series, []);
      }

      this.chart.subscribeCrosshairMove((param) => this._onCrosshairMove(param));
      this.chartEl.addEventListener("mouseleave", () => this._clearScrub());

      this._visibleRangeHandler = () => this._renderTradeOverlay();
      this.chart.timeScale().subscribeVisibleLogicalRangeChange(this._visibleRangeHandler);

      this.resizeObserver = new ResizeObserver(() => {
        if (!this.chart || !this.chartEl) return;
        this.chart.applyOptions({ width: this.chartEl.clientWidth });
        this._renderTradeOverlay();
      });
      this.resizeObserver.observe(this.chartEl);
    }

    _setSeriesMarkers(markers) {
      if (this.seriesMarkers) {
        this.seriesMarkers.setMarkers(markers);
        return;
      }
      if (this.series && typeof this.series.setMarkers === "function") {
        this.series.setMarkers(markers);
      }
    }

    _renderTradeOverlay() {
      if (!this.overlayEl || !this.chart) return;
      this.overlayEl.innerHTML = "";
      if (!this.tradeAnnotations.length) return;

      const timeScale = this.chart.timeScale();
      const chartWidth = this.chartEl.clientWidth;

      for (const trade of this.tradeAnnotations) {
        const time = toMarkerTime(trade.t, this.rangeKey);
        const x = timeScale.timeToCoordinate(time);
        if (x == null || x < 0 || x > chartWidth) continue;

        const isBuy = trade.action === "BUY";
        const side = isBuy ? "buy" : "sell";

        const line = document.createElement("div");
        line.className = `balance-trade-line ${side}`;
        line.style.left = `${x}px`;
        this.overlayEl.appendChild(line);

        const label = document.createElement("div");
        label.className = `balance-trade-label ${side}`;
        label.style.left = `${x}px`;
        label.textContent = trade.label || `${trade.action} ${trade.ticker}`;
        this.overlayEl.appendChild(label);
      }
    }

    setRange(key) {
      this.rangeKey = key;
      this.root.querySelectorAll(".range-btn").forEach((el) => {
        el.classList.toggle("active", el.dataset.range === key);
      });
    }

    getRange() {
      return this.rangeKey;
    }

    _applySeriesColors(up) {
      const line = up ? COLORS.up : COLORS.down;
      const top = up ? COLORS.upFill : COLORS.downFill;
      const opts = {
        lineColor: line,
        topColor: top,
        bottomColor: "transparent",
        color: line,
      };
      this.series.applyOptions(opts);
    }

    _renderHero(summary, rangeKey) {
      if (!summary) return;
      const up = (summary.change_usd ?? 0) >= 0;
      this.heroValue.textContent = fmtUsd(summary.current_value);
      this.heroChange.textContent = `${fmtSignedUsd(summary.change_usd)} (${fmtPct(summary.change_pct)})`;
      this.heroChange.classList.toggle("pos", up);
      this.heroChange.classList.toggle("neg", !up);
      this.heroPeriod.textContent = RANGE_LABELS[rangeKey] || rangeKey;
      this._applySeriesColors(up);
    }

    _renderMeta(payload) {
      if (!this.metaEl) return;
      const src =
        payload.source === "snapshots"
          ? "Measured balance · 7am–8pm ET"
          : payload.source === "live_only"
            ? "Live session — open dashboard 7am–8pm ET for history"
            : payload.source === "limited"
              ? "Limited history — snapshots record while dashboard is open"
              : payload.source || "";
      const pts = (payload.points || []).length;
      const ann = (payload.annotations || []).length;
      this.metaEl.textContent = `${src} · ${pts} pts · ${ann} trades`;
    }

    _onCrosshairMove(param) {
      if (!param.time || !param.seriesData || !this.series) {
        this._clearScrub();
        return;
      }
      const point = param.seriesData.get(this.series);
      if (!point || point.value == null) {
        this._clearScrub();
        return;
      }
      this.scrubbing = true;
      const timeLabel = fmtCrosshairTime(param.time, this.rangeKey);
      this.heroScrub.hidden = false;
      this.heroScrub.textContent = `${timeLabel} · ${fmtUsd(point.value)}`;
    }

    _clearScrub() {
      if (!this.scrubbing) return;
      this.scrubbing = false;
      if (this.heroScrub) {
        this.heroScrub.hidden = true;
        this.heroScrub.textContent = "";
      }
    }

    update(payload) {
      this.payload = payload;
      const rangeKey = payload.range || this.rangeKey;
      this.setRange(rangeKey);

      if (!this.series || !this.chart) {
        if (this.metaEl) this.metaEl.textContent = "Chart unavailable.";
        return;
      }

      this.tradeAnnotations = payload.annotations || [];

      if (!payload.points || !payload.points.length) {
        this.series.setData([]);
        this._setSeriesMarkers([]);
        this._renderTradeOverlay();
        this._renderHero(
          payload.summary || {
            current_value: 0,
            period_start_value: 0,
            change_usd: 0,
            change_pct: 0,
          },
          rangeKey,
        );
        this._renderMeta(payload);
        if (this.metaEl) {
          this.metaEl.textContent =
            "No balance history yet — snapshots record every ~5 min while the dashboard is open.";
        }
        return;
      }

      const data = toSeriesPoints(payload.points, rangeKey);
      this.series.setData(data);

      const markers = buildMarkers(this.tradeAnnotations, rangeKey, data);
      this._setSeriesMarkers(markers);

      const visible = visibleRangeFromWindow(payload, rangeKey);
      if (visible) {
        this.chart.timeScale().setVisibleRange(visible);
      }

      requestAnimationFrame(() => this._renderTradeOverlay());

      this._renderHero(payload.summary, rangeKey);
      this._renderMeta(payload);
      this._clearScrub();
    }

    destroy() {
      if (this.resizeObserver) this.resizeObserver.disconnect();
      if (this.chart && this._visibleRangeHandler) {
        this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this._visibleRangeHandler);
      }
      if (this.seriesMarkers && typeof this.seriesMarkers.detach === "function") {
        this.seriesMarkers.detach();
      }
      if (this.chart) this.chart.remove();
      this.chart = null;
      this.series = null;
      this.seriesMarkers = null;
    }
  }

  global.BalanceChart = BalanceChart;
})(typeof window !== "undefined" ? window : globalThis);
