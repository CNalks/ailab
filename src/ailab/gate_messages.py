"""Human-readable texts of the quality gate (English / Chinese / French).

``REASONS[code]`` and ``ACTIONS[code]`` map a language to a ``str.format`` template that is filled
with the measured metrics of the reason (missing keys render as ``?``). Machine clients should use
the ``code`` fields, never parse these strings.
"""

from __future__ import annotations

LANGS = ("en", "zh", "fr")

REASONS: dict[str, dict[str, str]] = {
    "kind_event_not_forecast": {
        "en": "Indicator type 'event' (sparse notifiable events) is monitored, not forecast.",
        "zh": "指标类型为“event”（稀疏的法定报告事件），只做监测，不做预测。",
        "fr": "Le type d'indicateur « event » (événements rares à déclaration obligatoire) est "
        "surveillé mais pas prévu.",
    },
    "negative_values": {
        "en": "The series contains negative values, which is invalid for this indicator type.",
        "zh": "序列含有负值，对该指标类型无效。",
        "fr": "La série contient des valeurs négatives, invalides pour ce type d'indicateur.",
    },
    "too_short": {
        "en": "History is too short: {n_periods} periods = {seasons:.1f} seasons "
        "(at least {min_seasons:g} needed).",
        "zh": "历史太短：{n_periods} 个周期 = {seasons:.1f} 个季节（至少需要 {min_seasons:g} 个）。",
        "fr": "Historique trop court : {n_periods} périodes = {seasons:.1f} saisons "
        "(au moins {min_seasons:g} requises).",
    },
    "too_many_gaps": {
        "en": "Too many missing values: {missing_pct:.1f}% missing, longest internal gap "
        "{longest_gap} periods (limits: {thr_missing_pct:.0f}% / {max_gap} periods).",
        "zh": "缺失值过多：缺失 {missing_pct:.1f}%，内部最长连续缺失 {longest_gap} 期"
        "（上限：{thr_missing_pct:.0f}% / {max_gap} 期）。",
        "fr": "Trop de valeurs manquantes : {missing_pct:.1f} % manquantes, plus longue lacune "
        "interne {longest_gap} périodes (seuils : {thr_missing_pct:.0f} % / {max_gap} périodes).",
    },
    "long_gap_at_end": {
        "en": "The most recent {trailing_gap} periods are missing: the series has no current "
        "data to forecast from.",
        "zh": "最近 {trailing_gap} 期缺失：序列没有可用于预测的最新数据。",
        "fr": "Les {trailing_gap} dernières périodes sont manquantes : pas de données récentes "
        "pour prévoir.",
    },
    "stale_data": {
        "en": "Data are {lag_periods} periods old (as of {as_of}); warn above {warn_periods}, "
        "refuse above {block_periods}.",
        "zh": "数据已过时 {lag_periods} 期（截至 {as_of}）；超过 {warn_periods} 期警告，"
        "超过 {block_periods} 期拒绝预测。",
        "fr": "Les données ont {lag_periods} périodes de retard (au {as_of}) ; avertissement "
        "au-delà de {warn_periods}, refus au-delà de {block_periods}.",
    },
    "constant_series": {
        "en": "The series is (almost) constant ({n_unique} distinct values, relative spread "
        "{rel_sd:.4f}); there is nothing to learn from.",
        "zh": "序列（几乎）为常数（{n_unique} 个不同取值，相对波动 {rel_sd:.4f}），无可学习的信息。",
        "fr": "La série est (presque) constante ({n_unique} valeurs distinctes, dispersion "
        "relative {rel_sd:.4f}) : rien à apprendre.",
    },
    "mostly_zeros": {
        "en": "Mostly zeros / intermittent: only {nonzero_pct:.0f}% of observed periods are > 0 "
        "(ADI {adi:.2f}, CV² {cv2:.2f}); needs at least {min_nonzero_pct:.0f}%.",
        "zh": "以零值为主 / 间歇性序列：仅 {nonzero_pct:.0f}% 的观测期大于 0"
        "（ADI {adi:.2f}，CV² {cv2:.2f}）；至少需要 {min_nonzero_pct:.0f}%。",
        "fr": "Surtout des zéros / série intermittente : seulement {nonzero_pct:.0f} % des "
        "périodes observées sont > 0 (ADI {adi:.2f}, CV² {cv2:.2f}) ; minimum "
        "{min_nonzero_pct:.0f} %.",
    },
    "structural_break": {
        "en": "Probable level shift at {break_date} ({shift:+.2f} in model units, "
        "sup-t {sup_t:.1f}); {where} the series, so the past may not represent the future.",
        "zh": "{break_date} 附近疑似水平突变（模型尺度 {shift:+.2f}，sup-t {sup_t:.1f}）；"
        "突变位于序列{where_zh}，过去可能不能代表未来。",
        "fr": "Rupture de niveau probable vers {break_date} ({shift:+.2f} en unités du modèle, "
        "sup-t {sup_t:.1f}) ; {where_fr} de la série : le passé peut ne pas représenter l'avenir.",
    },
    "unstable_variance": {
        "en": "Variance is unstable: residual spread in the last season is "
        "{var_ratio:.1f}x that of earlier history (Levene p={levene_p:.3g}).",
        "zh": "方差不稳定：最近一个季节的残差波动是此前历史的 {var_ratio:.1f} 倍"
        "（Levene p={levene_p:.3g}）。",
        "fr": "Variance instable : la dispersion des résidus de la dernière saison est "
        "{var_ratio:.1f} fois celle de l'historique (Levene p={levene_p:.3g}).",
    },
    "trend_nonstationary": {
        "en": "Non-stationary after removing seasonality (ADF p={adf_p:.3f}, KPSS p={kpss_p:.3f}, "
        "level drift {drift:+.2f}): this is hard to judge; seasonal epidemics alone do not "
        "trigger this, a trend or random walk does.",
        "zh": "去除季节性后仍不平稳（ADF p={adf_p:.3f}，KPSS p={kpss_p:.3f}，水平漂移 {drift:+.2f}）："
        "难以判断；仅有季节性流行不会触发该提示，趋势或随机游走才会。",
        "fr": "Non stationnaire après désaisonnalisation (ADF p={adf_p:.3f}, KPSS p={kpss_p:.3f}, "
        "dérive de niveau {drift:+.2f}) : difficile à juger ; la seule saisonnalité "
        "épidémique ne déclenche pas ce message, une tendance ou une marche aléatoire oui.",
    },
    "no_seasonal_signal_but_seasonal_expected": {
        "en": "A seasonal pattern is expected (period {season_length}) but the seasonal strength "
        "is only {seasonal_strength:.2f} (min {min_strength:.2f}); seasonal models add noise.",
        "zh": "预期有季节性（周期 {season_length}），但季节强度仅 {seasonal_strength:.2f}"
        "（下限 {min_strength:.2f}）；季节模型只会引入噪声。",
        "fr": "Une saisonnalité est attendue (période {season_length}) mais sa force n'est que de "
        "{seasonal_strength:.2f} (minimum {min_strength:.2f}) ; les modèles saisonniers "
        "ajoutent du bruit.",
    },
    "outlier_contamination": {
        "en": "{n_outliers} outlying periods ({outlier_pct:.1f}%, max deviation {max_dev:.2f} "
        "model units) distort fitting and interval calibration. Consider masking them.",
        "zh": "{n_outliers} 个离群周期（{outlier_pct:.1f}%，最大偏离 {max_dev:.2f} 模型单位）"
        "会扭曲拟合与区间校准。建议屏蔽（mask）这些时期。",
        "fr": "{n_outliers} périodes aberrantes ({outlier_pct:.1f} %, écart max {max_dev:.2f} "
        "unités du modèle) faussent l'ajustement et le calibrage des intervalles. "
        "Envisagez de les masquer.",
    },
    "reporting_delay_tail_incomplete": {
        "en": "The most recent {tail_weeks} period(s) look incompletely reported "
        "({tail_evidence}); they are {tail_action}.",
        "zh": "最近 {tail_weeks} 期数据看起来尚未报全（{tail_evidence}）；已{tail_action_zh}。",
        "fr": "Les {tail_weeks} dernière(s) période(s) semblent incomplètement déclarées "
        "({tail_evidence}) ; elles sont {tail_action_fr}.",
    },
    "insufficient_backtest": {
        "en": "Backtest too small to judge skill: {n_windows} origins spanning "
        "{seasons_covered:.1f} seasons (need {min_windows} origins).",
        "zh": "回测样本不足以判断预测技巧：{n_windows} 个起点，跨度 {seasons_covered:.1f} 个季节"
        "（至少需要 {min_windows} 个起点）。",
        "fr": "Rétro-test trop petit pour juger la compétence : {n_windows} origines sur "
        "{seasons_covered:.1f} saisons ({min_windows} origines requises).",
    },
    "backtest_worse_than_baseline": {
        "en": "The best model does not beat the seasonal-naive baseline in the rolling-origin "
        "backtest (MAE ratio {rel_mae:.2f}, must be < {max_rel_mae:.2f}).",
        "zh": "滚动起点回测中，最佳模型未能优于季节朴素基线（MAE 比 {rel_mae:.2f}，须 < "
        "{max_rel_mae:.2f}）。",
        "fr": "Le meilleur modèle ne bat pas la référence saisonnière naïve en rétro-test à "
        "origine glissante (rapport MAE {rel_mae:.2f}, doit être < {max_rel_mae:.2f}).",
    },
    "marginal_skill": {
        "en": "Skill over the baseline is marginal (MAE ratio {rel_mae:.2f} ≥ {marginal_rel_mae:.2f}); "
        "the forecast is shortened and intervals widened.",
        "zh": "相对基线的预测技巧很有限（MAE 比 {rel_mae:.2f} ≥ {marginal_rel_mae:.2f}）；"
        "已缩短预测期并加宽区间。",
        "fr": "Le gain sur la référence est marginal (rapport MAE {rel_mae:.2f} ≥ "
        "{marginal_rel_mae:.2f}) ; horizon raccourci et intervalles élargis.",
    },
    "skill_horizon_too_short": {
        "en": "The model beats the baseline for only {h_ok} leading horizon(s); "
        "{min_horizons} are required.",
        "zh": "模型仅在前 {h_ok} 个预测步长上优于基线；至少需要 {min_horizons} 个。",
        "fr": "Le modèle ne bat la référence que sur {h_ok} horizon(s) initial(aux) ; "
        "{min_horizons} requis.",
    },
    "no_skill_beyond_persistence": {
        "en": "The best model is plain persistence (Naive): no learnable seasonal or trend "
        "structure beyond 'same as last period'.",
        "zh": "最佳模型仅是持平（Naive）：除“与上一期相同”外没有可学习的季节或趋势结构。",
        "fr": "Le meilleur modèle est la simple persistance (Naive) : aucune structure "
        "saisonnière ou tendancielle exploitable au-delà de « comme la dernière période ».",
    },
    "interval_undercoverage": {
        "en": "Prediction intervals under-cover in the backtest: empirical 80% coverage "
        "{cov80:.0%} (needs {min_cov80:.0%}).",
        "zh": "回测中预测区间覆盖不足：80% 区间经验覆盖率 {cov80:.0%}（要求 {min_cov80:.0%}）。",
        "fr": "Les intervalles de prévision sous-couvrent en rétro-test : couverture empirique "
        "à 80 % de {cov80:.0%} (minimum {min_cov80:.0%}).",
    },
    "short_backtest_span": {
        "en": "The backtest spans only {seasons_covered:.1f} seasons (< {min_seasons:g}); skill "
        "and calibration are less reliable.",
        "zh": "回测仅跨 {seasons_covered:.1f} 个季节（< {min_seasons:g}）；技巧与校准的可靠性较低。",
        "fr": "Le rétro-test ne couvre que {seasons_covered:.1f} saisons (< {min_seasons:g}) ; "
        "compétence et calibrage moins fiables.",
    },
    "short_history": {
        "en": "Short history ({seasons:.1f} seasons, < {warn_seasons:g}): estimates are noisy.",
        "zh": "历史偏短（{seasons:.1f} 个季节，< {warn_seasons:g}）：估计噪声较大。",
        "fr": "Historique court ({seasons:.1f} saisons, < {warn_seasons:g}) : estimations bruitées.",
    },
    "mostly_zeros_warn": {
        "en": "Many zeros: {nonzero_pct:.0f}% of observed periods are > 0 (< {warn_nonzero_pct:.0f}%).",
        "zh": "零值较多：仅 {nonzero_pct:.0f}% 的观测期大于 0（< {warn_nonzero_pct:.0f}%）。",
        "fr": "Beaucoup de zéros : {nonzero_pct:.0f} % des périodes observées sont > 0 "
        "(< {warn_nonzero_pct:.0f} %).",
    },
    "zero_runs": {
        "en": "A run of {zero_run} exact zeros inside an otherwise active series "
        "(possible implicit zero-fill of missing data).",
        "zh": "在其他时期有活动的序列中出现连续 {zero_run} 期的精确零值（可能是缺失数据被隐式补零）。",
        "fr": "Une suite de {zero_run} zéros exacts dans une série par ailleurs active "
        "(remplissage implicite de données manquantes possible).",
    },
    "gate_check_skipped": {
        "en": "Check '{check}' skipped: {why}.",
        "zh": "已跳过检查“{check}”：{why}。",
        "fr": "Contrôle « {check} » ignoré : {why}.",
    },
    # ---- alert gate --------------------------------------------------------------------
    "alert_baseline_too_short": {
        "en": "Cannot evaluate this alert: {n_periods} periods, but baseline {baseline} + guard "
        "{guard} + 1 = {needed} are needed.",
        "zh": "无法评估该预警：仅 {n_periods} 期，而基线 {baseline} + 保护带 {guard} + 1 = {needed} 期是最低要求。",
        "fr": "Alerte non évaluable : {n_periods} périodes, alors que base {baseline} + garde "
        "{guard} + 1 = {needed} sont nécessaires.",
    },
    "alert_short_history": {
        "en": "Short history for this alert: {n_periods} periods (full confidence needs "
        "{needed}, i.e. {min_eval} evaluable periods beyond the baseline); signals are shown "
        "with low confidence.",
        "zh": "该预警的历史偏短：{n_periods} 期（要达到完全置信需 {needed} 期，即基线之外再有 "
        "{min_eval} 个可评估周期）；信号以低置信度显示。",
        "fr": "Historique court pour cette alerte : {n_periods} périodes (pleine confiance : "
        "{needed}, soit {min_eval} périodes évaluables après la base) ; signaux à faible confiance.",
    },
    "alert_baseline_gappy": {
        "en": "The baseline window of the latest period has only {obs} observed of {baseline} weeks "
        "(need {needed}); no alert is evaluated.",
        "zh": "最新一期的基线窗口 {baseline} 周中仅有 {obs} 周有观测（需要 {needed}）；不评估预警。",
        "fr": "La fenêtre de base de la dernière période n'a que {obs} semaines observées sur "
        "{baseline} (il en faut {needed}) ; aucune alerte évaluée.",
    },
    "alert_baseline_low_counts": {
        "en": "The baseline is mostly zeros / very low counts (mean {mean:.2f}, "
        "{nonzero_pct:.0f}% non-zero): z-score methods are unreliable.",
        "zh": "基线几乎全为零/计数极低（均值 {mean:.2f}，非零占 {nonzero_pct:.0f}%）：z 分数类方法不可靠。",
        "fr": "La base est presque nulle / très faibles effectifs (moyenne {mean:.2f}, "
        "{nonzero_pct:.0f} % non nuls) : les méthodes à score z ne sont pas fiables.",
    },
    "alert_baseline_constant": {
        "en": "The baseline is constant; the score uses a variance floor, so signals are "
        "driven by the floor rather than by data.",
        "zh": "基线为常数；得分使用了方差下限，因此信号由下限而非数据决定。",
        "fr": "La base est constante ; le score utilise un plancher de variance, les signaux "
        "dépendent du plancher plutôt que des données.",
    },
    "alert_baseline_contaminated": {
        "en": "The baseline window contains an outlying period (likely a previous outbreak), "
        "which inflates the expected value and hides signals.",
        "zh": "基线窗口含离群期（可能是此前的暴发），会抬高期望值并掩盖信号。",
        "fr": "La fenêtre de base contient une période aberrante (probable épisode précédent) "
        "qui gonfle la valeur attendue et masque les signaux.",
    },
    "alert_method_not_applicable": {
        "en": "{method} needs counts (Poisson model); it is not applicable to '{kind}' series. "
        "Use ears_c2 or cusum.",
        "zh": "{method} 需要计数数据（泊松模型），不适用于“{kind}”类序列。请使用 ears_c2 或 cusum。",
        "fr": "{method} requiert des comptages (modèle de Poisson) ; non applicable aux séries "
        "« {kind} ». Utilisez ears_c2 ou cusum.",
    },
    "alert_not_enough_seasons": {
        "en": "{method} needs {min_seasons} full seasons of history; only {seasons:.1f} available.",
        "zh": "{method} 需要 {min_seasons} 个完整季节的历史；目前仅 {seasons:.1f} 个。",
        "fr": "{method} nécessite {min_seasons} saisons complètes d'historique ; seulement "
        "{seasons:.1f} disponibles.",
    },
}

ACTIONS: dict[str, dict[str, str]] = {
    "none": {
        "en": "No action needed.",
        "zh": "无需处理。",
        "fr": "Aucune action nécessaire.",
    },
    "collect_more_history": {
        "en": "Collect more history (at least {min_seasons:g} full seasons) before forecasting.",
        "zh": "先积累更多历史（至少 {min_seasons:g} 个完整季节）再做预测。",
        "fr": "Rassemblez plus d'historique (au moins {min_seasons:g} saisons complètes) "
        "avant de prévoir.",
    },
    "repair_gaps": {
        "en": "Fill or repair the missing periods at the source (never zero-fill), "
        "or provide a series without the gap.",
        "zh": "在数据源处补全缺失周期（不要补零），或提供不含缺口的序列。",
        "fr": "Complétez ou réparez les périodes manquantes à la source (jamais par des zéros) "
        "ou fournissez une série sans lacune.",
    },
    "wait_for_data_update": {
        "en": "Wait for the next data update, then re-run the gate.",
        "zh": "等待数据更新后重新运行质量闸门。",
        "fr": "Attendez la prochaine mise à jour des données, puis relancez la porte qualité.",
    },
    "monitor_only": {
        "en": "Use the series for monitoring and alerts only; do not publish a forecast.",
        "zh": "该序列只用于监测与预警，不发布预测。",
        "fr": "Utilisez la série pour la surveillance et les alertes seulement ; "
        "ne publiez pas de prévision.",
    },
    "review_series_definition": {
        "en": "Check the series definition and units; a constant or all-zero series is "
        "usually a data-pipeline problem.",
        "zh": "检查序列定义与单位；常数或全零序列通常是数据管道问题。",
        "fr": "Vérifiez la définition et les unités de la série ; une série constante ou nulle "
        "est généralement un problème de chaîne de données.",
    },
    "review_structural_break": {
        "en": "Investigate the level shift (definition change, new reporting site, policy); "
        "if confirmed, restrict the history to the period after it.",
        "zh": "排查水平突变的原因（口径变更、新增报告点、政策）；确认后只使用突变之后的历史。",
        "fr": "Cherchez la cause de la rupture (changement de définition, nouveau site, politique) ; "
        "si elle est confirmée, limitez l'historique à la période suivante.",
    },
    "mask_outlier_period": {
        "en": "Mask known disruption periods (e.g. COVID-19) via `mask_periods` so they do not "
        "affect fitting or interval calibration.",
        "zh": "通过 `mask_periods` 屏蔽已知的异常时期（如 COVID-19），避免影响拟合与区间校准。",
        "fr": "Masquez les périodes perturbées connues (p. ex. COVID-19) via `mask_periods` "
        "pour qu'elles n'influencent ni l'ajustement ni les intervalles.",
    },
    "set_reporting_lag": {
        "en": "Set `reporting_lag_weeks` for this series so provisional recent weeks are "
        "excluded from fitting, or wait until they are final.",
        "zh": "为该序列设置 `reporting_lag_weeks`，把尚未定稿的近期周排除在拟合之外，或等待其定稿。",
        "fr": "Définissez `reporting_lag_weeks` pour cette série afin d'exclure les semaines "
        "récentes provisoires de l'ajustement, ou attendez qu'elles soient définitives.",
    },
    "use_limited_forecast": {
        "en": "Use the limited forecast (short horizon / wider intervals / baseline model) "
        "and treat it with caution.",
        "zh": "使用受限预测（较短预测期 / 更宽区间 / 基线模型），并谨慎对待。",
        "fr": "Utilisez la prévision limitée (horizon court / intervalles élargis / modèle de "
        "référence) avec prudence.",
    },
    "interpret_with_care": {
        "en": "The forecast passed the backtest, but the series is hard to judge "
        "(non-stationary / unstable); treat horizons beyond the first few weeks with care.",
        "zh": "预测通过了回测，但该序列难以判断（非平稳/不稳定）；对较远的预测步长请谨慎看待。",
        "fr": "La prévision a passé le rétro-test, mais la série est difficile à juger "
        "(non stationnaire / instable) ; prudence au-delà des premières semaines.",
    },
    "review_manually": {
        "en": "This is hard to judge automatically; have an analyst review the series.",
        "zh": "难以自动判断；请分析人员人工审核该序列。",
        "fr": "Difficile à juger automatiquement ; faites relire la série par un analyste.",
    },
    "extend_backtest": {
        "en": "Provide more history so that the rolling-origin backtest has enough origins.",
        "zh": "提供更多历史，使滚动起点回测有足够的起点。",
        "fr": "Fournissez plus d'historique pour que le rétro-test ait assez d'origines.",
    },
}


def render(table: dict[str, dict[str, str]], code: str, metrics: dict) -> dict[str, str]:
    """All languages of ``code`` formatted with ``metrics`` (unknown placeholders become ``?``)."""

    class _Safe(dict):
        def __missing__(self, key):
            return "?"

    tpl = table.get(code)
    if tpl is None:
        return {lang: code for lang in LANGS}
    out = {}
    for lang in LANGS:
        try:
            out[lang] = tpl[lang].format_map(_Safe(metrics))
        except (ValueError, KeyError, IndexError, TypeError):
            out[lang] = tpl[lang]
    return out
