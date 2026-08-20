# agent/tools.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.holtwinters import ExponentialSmoothing, SimpleExpSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.forecasting.theta import ThetaModel
from sklearn.ensemble import RandomForestRegressor

# ---------------- utils ----------------

def _to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")

def _infer_freq(idx: pd.Index) -> str:
    """
    Best-effort freq inference used where we *need* a freq.
    Falls back to 'D' if pandas can't infer.
    """
    try:
        f = pd.infer_freq(idx)
        if f: return f
    except Exception:
        pass
    return "D"

def _seasonal_period(idx: pd.Index) -> Optional[int]:
    f = (_infer_freq(idx) or "").upper()
    if f.startswith("H"): return 24
    if f.startswith("D"): return 7
    if "W" in f:         return 52
    if "M" in f:         return 12
    if "Q" in f:         return 4
    return None

def _future_index(last_ts: pd.Timestamp, horizon: int, freq: str) -> pd.DatetimeIndex:
    return pd.date_range(start=last_ts, periods=int(horizon)+1, freq=freq)[1:]

def _winsorize(s: pd.Series, z=4.0) -> pd.Series:
    x = s.copy()
    mu, sd = x.mean(), x.std(ddof=0) + 1e-9
    return x.clip(lower=mu - z*sd, upper=mu + z*sd)

def _auto_transform_fit(s: pd.Series) -> Dict:
    info = {"kind":"identity"}
    if (s > 0).all() and (s.skew() > 1.0 or (s.std()/(abs(s.mean())+1e-9) > 1.0)):
        info["kind"] = "log1p"
    return info

def _t_apply(s: pd.Series, tf: Dict) -> pd.Series:
    return np.log1p(s) if tf["kind"] == "log1p" else s.copy()

def _t_inv(s: pd.Series, tf: Dict) -> pd.Series:
    return np.expm1(s) if tf["kind"] == "log1p" else s.copy()

def _regularize_index(s: pd.Series) -> pd.Series:
    """
    Ensure a regular DateTimeIndex with a set frequency to avoid
    statsmodels ValueWarning about missing freq. If pandas can infer a
    frequency, use it and fill any gaps by interpolation.
    """
    if not isinstance(s.index, pd.DatetimeIndex) or s.empty:
        return s
    # drop duplicates, keep last
    s = s[~s.index.duplicated(keep="last")]
    # only set freq if pandas can infer one; otherwise leave as-is
    try:
        pfreq = pd.infer_freq(s.index)
    except Exception:
        pfreq = None
    if pfreq:
        s = s.asfreq(pfreq)
        # fill gaps created by asfreq
        if s.isna().any():
            # linear interpolation, then back/forward fill ends
            s = s.interpolate(limit_direction="both")
            s = s.ffill().bfill()
    return s

# ---------------- load & profile ----------------

def load_dataset(path: Path, date_col: str = "date") -> Tuple[pd.DataFrame, List[str]]:
    path = Path(path)
    if not path.exists(): raise FileNotFoundError(f"Dataset not found: {path}")
    if path.suffix.lower() in {".xlsx",".xls"}:
        df = pd.read_excel(path)
    else:
        try: df = pd.read_csv(path)
        except UnicodeDecodeError: df = pd.read_csv(path, encoding="latin-1")

    if date_col not in df.columns:
        guess = next((c for c in df.columns if "date" in c.lower()), None)
        if not guess: raise ValueError("No date column found; set date_col.")
        date_col = guess

    df = df.copy()
    df[date_col] = _to_dt(df[date_col])
    df = df.dropna(subset=[date_col]).sort_values(date_col)
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    return df, num_cols

def profile_trends(df: pd.DataFrame, date_col: str, target: str) -> Dict:
    s = df[[date_col,target]].dropna().sort_values(date_col).set_index(date_col)[target].astype(float)
    s = _regularize_index(s)
    if s.empty: return {"rows":0}
    stl = STL(s, period=_seasonal_period(s.index) or 7, robust=True).fit()
    seasonal_strength = max(0.0, 1.0 - np.var(stl.resid) / (np.var(stl.resid + stl.seasonal) + 1e-9))
    slope = float(np.polyfit(range(len(stl.trend.dropna())), stl.trend.dropna().values, 1)[0])
    return {"rows":int(len(s)), "start":s.index.min().date().isoformat(), "end":s.index.max().date().isoformat(),
            "mean":float(s.mean()), "std":float(s.std()), "seasonal_strength":float(seasonal_strength), "trend_slope":slope}

def quality_report(df: pd.DataFrame, date_col: str, target: str) -> pd.DataFrame:
    s = df[[date_col,target]].dropna().sort_values(date_col).set_index(date_col)[target].astype(float)
    s = _regularize_index(s)
    if s.empty: return pd.DataFrame({"n_points":[0]})
    freq = _infer_freq(s.index)
    full = pd.date_range(s.index.min(), s.index.max(), freq=freq)
    return pd.DataFrame({
        "n_points":[len(s)], "freq":[freq], "start":[s.index.min()], "end":[s.index.max()],
        "n_missing":[int(len(full.difference(s.index)))],
        "n_dupes":[0]  # deduped in _regularize_index
    })

# ---------------- models ----------------

def _ets_forecast(y: pd.Series, horizon: int, params: Dict):
    y = _winsorize(y.dropna())
    tf = _auto_transform_fit(y); yt = _t_apply(y, tf)

    seas = params.get("seasonal_periods", _seasonal_period(yt.index) or 7)
    trend = params.get("trend","add"); seasonal = params.get("seasonal","add")
    damped = bool(params.get("damped", False))
    try:
        fit = ExponentialSmoothing(yt, trend=trend, seasonal=seasonal, seasonal_periods=seas,
                                   damped_trend=damped, initialization_method="estimated").fit()
    except Exception:
        try:
            fit = ExponentialSmoothing(yt, trend="add", seasonal=None, initialization_method="estimated").fit()
        except Exception:
            fit = SimpleExpSmoothing(yt).fit()

    freq = yt.index.freqstr or _infer_freq(yt.index)
    idx = _future_index(yt.index.max(), horizon, freq)
    yhat_t = pd.Series(fit.forecast(int(horizon)).values, index=idx, name="y_hat")
    return _t_inv(yhat_t, tf), (None, None)

def _sarimax_fit(y: pd.Series, order, seas_order=None):
    return SARIMAX(y, order=order, seasonal_order=seas_order or (0,0,0,0),
                   enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)

def _sarimax_forecast(y: pd.Series, horizon: int, params: Dict, seasonal: bool):
    y = _winsorize(y.dropna()); tf = _auto_transform_fit(y); yt = _t_apply(y, tf)
    sp = params.get("seasonal_period", _seasonal_period(yt.index) if seasonal else None)
    order = params.get("order"); seas_order = params.get("seasonal_order")

    if (order is None) and (seas_order is None):
        p,d,q = [0,1,2], [0,1], [0,1,2]
        P = [0,1] if seasonal and sp else [0]; D=[0,1] if seasonal and sp else [0]; Q=[0,1] if seasonal and sp else [0]
        best_aic, best = np.inf, None
        for i in p:
            for j in d:
                for k in q:
                    for I in P:
                        for J in D:
                            for K in Q:
                                try:
                                    res = _sarimax_fit(yt, (i,j,k), (I,J,K,sp) if seasonal and sp else None)
                                    if res.aic < best_aic:
                                        best_aic, best = res.aic, res
                                except Exception:
                                    pass
        if best is None:
            best = _sarimax_fit(yt, (1,1,0), None)
    else:
        try:
            best = _sarimax_fit(yt, tuple(order or (1,1,1)),
                                tuple(seas_order) if seas_order else ((1,1,1,sp) if seasonal and sp else None))
        except Exception:
            best = _sarimax_fit(yt, (1,1,0), None)

    freq = yt.index.freqstr or _infer_freq(yt.index)
    idx = _future_index(yt.index.max(), horizon, freq)
    fc = best.get_forecast(steps=int(horizon))
    mean_t = pd.Series(fc.predicted_mean.values, index=idx, name="y_hat")
    try:
        ci = fc.conf_int(alpha=0.05).to_numpy()
        lo_t = pd.Series(ci[:,0], index=idx); hi_t = pd.Series(ci[:,1], index=idx)
        return _t_inv(mean_t, tf), (_t_inv(lo_t, tf), _t_inv(hi_t, tf))
    except Exception:
        return _t_inv(mean_t, tf), (None, None)

def _theta_forecast(y: pd.Series, horizon: int, params: Dict):
    """Use ThetaModel.fit() *without* theta= (statsmodels 0.14.x)."""
    y = _winsorize(y.dropna()); tf = _auto_transform_fit(y); yt = _t_apply(y, tf)
    period = params.get("period", _seasonal_period(yt.index) or 7)
    try:
        tm = ThetaModel(yt, period=period, deseasonalize=True); fit = tm.fit()
    except Exception:
        tm = ThetaModel(yt, period=period, deseasonalize=False); fit = tm.fit()
    freq = yt.index.freqstr or _infer_freq(yt.index)
    idx = _future_index(yt.index.max(), horizon, freq)
    yhat_t = pd.Series(fit.forecast(int(horizon)).values, index=idx, name="y_hat")
    return _t_inv(yhat_t, tf), (None, None)

def _ml_forecast(y: pd.Series, horizon: int, params: Dict):
    y = _winsorize(y.dropna()); tf = _auto_transform_fit(y); yt = _t_apply(y, tf)
    lags = params.get("lags", [1,3,7,14,28])
    if isinstance(lags, str): lags = [int(x) for x in lags.split(",") if x.strip()]
    lags = [int(x) for x in lags]
    n_estimators = int(params.get("n_estimators", 400))

    df = pd.DataFrame({"y": yt})
    for L in lags: df[f"lag_{L}"] = df["y"].shift(L)
    df["dow"] = df.index.dayofweek; df["mo"] = df.index.month; df["q"] = df.index.quarter
    df["r7"] = df["y"].rolling(7, min_periods=3).mean()
    df["r28"] = df["y"].rolling(28, min_periods=7).mean()
    df = df.dropna()
    if df.empty:
        freq = yt.index.freqstr or _infer_freq(yt.index)
        idx = _future_index(yt.index.max(), horizon, freq)
        last = float(yt.iloc[-1]); yhat_t = pd.Series([last]*horizon, index=idx)
        return _t_inv(yhat_t, tf), (None, None)

    X = df.drop(columns=["y"]); Y = df["y"]
    rf = RandomForestRegressor(n_estimators=n_estimators, random_state=42, n_jobs=-1)
    rf.fit(X, Y)

    freq = yt.index.freqstr or _infer_freq(yt.index)
    idx = _future_index(yt.index.max(), horizon, freq)
    preds_t = []
    history = yt.copy()
    for i in range(int(horizon)):
        z = pd.concat([history, pd.Series(preds_t, index=idx[:len(preds_t)])])
        row = {f"lag_{L}": float(z.iloc[-L]) if len(z)>=L else float(z.iloc[-1]) for L in lags}
        d = idx[i]
        row.update({"dow":d.dayofweek, "mo":d.month, "q":d.quarter})
        row["r7"]  = float(pd.Series(z).rolling(7, min_periods=3).mean().iloc[-1])
        row["r28"] = float(pd.Series(z).rolling(28, min_periods=7).mean().iloc[-1])
        preds_t.append(float(rf.predict(pd.DataFrame([row], columns=X.columns))[0]))
    yhat_t = pd.Series(preds_t, index=idx, name="y_hat")
    return _t_inv(yhat_t, tf), (None, None)

def _hybrid_forecast(y: pd.Series, horizon: int, params: Dict):
    y1,_ = _theta_forecast(y, horizon, params.get("theta", {}))
    y2,_ = _sarimax_forecast(y, horizon, params.get("sarimax", {}), seasonal=True)
    return (y1.add(y2, fill_value=0)/2.0), (None, None)

# ---------------- public forecast ----------------

def forecast(df: pd.DataFrame, date_col: str, target: str, horizon: int,
             method: str = "auto", params: Optional[Dict] = None) -> Dict:
    params = params or {}
    d = df[[date_col,target]].copy(); d[date_col] = _to_dt(d[date_col])
    s = d.dropna().sort_values(date_col).set_index(date_col)[target].astype(float)

    # NEW: regularize date index to carry a frequency (silences ValueWarning)
    s = _regularize_index(s)

    if s.empty:
        idx = pd.date_range(pd.Timestamp.now(), periods=horizon+1, freq="D")[1:]
        return {"history": s, "forecast": pd.Series([np.nan]*horizon, index=idx), "ci": (None,None), "method": method}

    method = (method or "auto").lower()
    if method == "auto":
        method = "hybrid" if len(s) >= 180 else "theta"

    if method == "ets":
        yhat, ci = _ets_forecast(s, horizon, params)
    elif method == "sarimax_seasonal":
        yhat, ci = _sarimax_forecast(s, horizon, params, seasonal=True)
    elif method in ("sarimax_ns","arima"):
        yhat, ci = _sarimax_forecast(s, horizon, params, seasonal=False)
    elif method == "theta":
        yhat, ci = _theta_forecast(s, horizon, params)
    elif method == "ml":
        yhat, ci = _ml_forecast(s, horizon, params)
    elif method == "hybrid":
        yhat, ci = _hybrid_forecast(s, horizon, params)
    else:
        yhat, ci = _ets_forecast(s, horizon, {}); method = "ets"

    if not isinstance(ci, tuple) or len(ci)!=2: ci=(None,None)
    return {"history": s, "forecast": yhat, "ci": ci, "method": method}

# ---------------- multi-fold cross‑validation & ensemble ----------------

def _smape(y_true, y_pred):
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred)
    denom = (np.abs(y_true) + np.abs(y_pred))/2.0
    denom = np.where(denom==0, 1.0, denom)
    return float(np.mean(np.abs(y_pred - y_true)/denom) * 100.0)

def crossval_compare(df: pd.DataFrame, date_col: str, target: str,
                     horizon: int, folds: int = 3,
                     candidate_methods: Optional[List[str]] = None,
                     method_params: Optional[Dict[str,Dict]] = None):
    candidate_methods = candidate_methods or ["ets","sarimax_seasonal","sarimax_ns","theta","ml","hybrid"]
    method_params = method_params or {}
    d = df[[date_col,target]].copy(); d[date_col] = _to_dt(d[date_col])
    s = d.dropna().sort_values(date_col).set_index(date_col)[target].astype(float)
    s = _regularize_index(s)
    n = len(s)
    total = folds * horizon
    if n < max(40, total + 10):
        folds = max(1, min(folds, (n-10)//max(1,horizon)))

    rows = []; per_fold = {m: [] for m in candidate_methods}
    for f in range(folds, 0, -1):
        split = n - f*horizon
        train = s.iloc[:split]; test = s.iloc[split: split+horizon]
        for m in candidate_methods:
            try:
                res = forecast(train.reset_index(), date_col, target, horizon=len(test), method=m,
                               params=method_params.get(m, {}))
                yh = pd.Series(res["forecast"].values, index=test.index).reindex(test.index)
                sm = _smape(test.values, yh.values)
                rows.append({"fold":f, "model":m.upper(), "sMAPE":sm})
                per_fold[m].append(sm)
            except Exception:
                rows.append({"fold":f, "model":m.upper(), "sMAPE":np.inf})
                per_fold[m].append(np.inf)

    agg = []
    for m in candidate_methods:
        vals = [v for v in per_fold[m] if np.isfinite(v)]
        agg.append({"model": m.upper(), "mean_sMAPE": (np.mean(vals) if vals else np.inf),
                    "folds": len(vals)})
    lb = pd.DataFrame(agg).sort_values("mean_sMAPE").reset_index(drop=True)

    weights = {}
    if not lb.empty and np.isfinite(lb.iloc[0]["mean_sMAPE"]):
        top2 = lb.iloc[:2]
        inv = np.array([1.0/max(1e-6, top2.iloc[0]["mean_sMAPE"]),
                        1.0/max(1e-6, top2.iloc[1]["mean_sMAPE"]) if len(top2)>1 else 0.0])
        if inv.sum() > 0:
            inv = inv / inv.sum()
            weights = {top2.iloc[0]["model"].lower(): float(inv[0])}
            if len(top2) > 1: weights[top2.iloc[1]["model"].lower()] = float(inv[1])

    return lb, pd.DataFrame(rows).sort_values(["fold","sMAPE"]), weights

def ensemble_forecast(df: pd.DataFrame, date_col: str, target: str, horizon: int,
                      weights: Dict[str,float]) -> Dict:
    parts = []
    for method, w in weights.items():
        r = forecast(df, date_col, target, horizon, method=method, params={})
        parts.append((r["forecast"], w))
    if not parts:
        return forecast(df, date_col, target, horizon, method="auto")
    idx = parts[0][0].index
    y = sum(w * p.reindex(idx).fillna(method="ffill").fillna(method="bfill") for p,w in parts)
    return {"history": forecast(df, date_col, target, 1, method="auto")["history"], "forecast": y, "ci": (None,None),
            "method": "ensemble(" + "+".join([f"{k}:{v:.2f}" for k,v in weights.items()]) + ")"}

# ---------------- anomalies & plots & export ----------------

def detect_anomalies(df: pd.DataFrame, date_col: str, target: str, window_days: int = 90) -> pd.DataFrame:
    s = df[[date_col,target]].dropna().sort_values(date_col).set_index(date_col)[target].astype(float)
    s = _regularize_index(s)
    if s.empty: return pd.DataFrame(columns=["date","y","z","is_anomaly"])
    stl = STL(s, period=_seasonal_period(s.index) or 7, robust=True).fit()
    resid = stl.resid
    z = (resid - resid.rolling(14, min_periods=7).mean()) / (resid.rolling(14, min_periods=7).std() + 1e-9)
    x = pd.DataFrame({"date": s.index, "y": s.values, "z": z.values})
    x["is_anomaly"] = x["z"].abs() >= 3.0
    if window_days and len(x) > window_days: x = x.iloc[-window_days:]
    return x

def decompose_stl(df: pd.DataFrame, date_col: str, target: str):
    s = df[[date_col,target]].dropna().sort_values(date_col).set_index(date_col)[target].astype(float)
    s = _regularize_index(s)
    if s.empty: return s, s, s, s
    p = _seasonal_period(s.index) or 7; res = STL(s, period=p, robust=True).fit()
    return s, res.trend, res.seasonal, res.resid

def plot_decomposition(s, tr, se, re, title="STL Decomposition"):
    figs=[]
    for name, series in [("Observed",s),("Trend",tr),("Seasonal",se),("Residual",re)]:
        fig = go.Figure(); fig.add_scatter(x=series.index, y=series.values, mode="lines", name=name)
        fig.update_layout(title=f"{title} — {name}", xaxis_title="Date", yaxis_title=name); figs.append(fig)
    return figs

def plot_forecast(history: pd.Series, forecast: pd.Series, title: str = "Forecast") -> go.Figure:
    fig = go.Figure()
    if isinstance(history, pd.Series) and not history.empty:
        fig.add_scatter(x=history.index, y=history.values, mode="lines", name="History")
    if isinstance(forecast, pd.Series) and not forecast.empty:
        fig.add_scatter(x=forecast.index, y=forecast.values, mode="lines", name="Forecast")
    fig.update_layout(title=title, xaxis_title="Date", yaxis_title="Value")
    return fig

def plot_anomalies(anom: pd.DataFrame, title: str = "Anomalies") -> go.Figure:
    fig = go.Figure()
    if not anom.empty:
        fig.add_scatter(x=anom["date"], y=anom["y"], mode="lines", name="Value")
        flags = anom[anom["is_anomaly"]]
        if not flags.empty:
            fig.add_scatter(x=flags["date"], y=flags["y"], mode="markers", name="Anomaly")
    fig.update_layout(title=title)
    return fig

def plot_backtest(test: pd.Series, preds_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=test.index, y=test.values, mode="lines", name="Actual")
    for col in preds_df.columns:
        fig.add_scatter(x=preds_df.index, y=preds_df[col].values, mode="lines", name=col)
    fig.update_layout(title="Backtest — Actual vs Pred", xaxis_title="Date", yaxis_title="Value")
    return fig

def export_run(history: pd.Series, forecast: pd.Series, anomalies: Optional[pd.DataFrame], target: str) -> Path:
    out = Path("artifacts")/f"run_{target}_{pd.Timestamp.now(tz='UTC'):%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": history.index, "y": history.values}).to_csv(out/"history.csv", index=False)
    pd.DataFrame({"date": forecast.index, "y_hat": forecast.values}).to_csv(out/"forecast.csv", index=False)
    if anomalies is not None and not anomalies.empty:
        anomalies.to_csv(out/"anomalies.csv", index=False)
    (out/"report.md").write_text(
        f"# Forecast Report — {target}\n\nPoints={len(history)}; Horizon={len(forecast)}\n", encoding="utf-8"
    )
    return out
