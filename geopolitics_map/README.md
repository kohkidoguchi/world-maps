# 世界政治・地政学マップ（geopolitics_map）

「誰が・誰に・何をしたか」を機械コード化した GDELT のイベントを、主体の規模・強度・注目度・新規性で重みづけして毎日世界地図に落とし、
その上に **市場が織り込む確率（予測市場）・制裁・同盟・軍事力・統治の質** を重ねる。公開データのみで動く。

```
GDELT 2.0 Events（15分ごと, CAMEO）   Polymarket Gamma API（地政学・選挙タグ）   OFAC SDN / EU 制裁リスト / 国連安保理 制裁リスト
世界銀行 WDI・WGI（軍事費・兵員・人口・GDP・ガバナンス6指標）   Wikipedia 国政選挙カレンダー   国連プレスリリース RSS   同盟・ブロック表（手動）
        │
        ▼  pipeline/  →  gdelt.py（取得・政治的事象の抽出・重みづけ・日次ストア）, markets.py, sanctions.py, structure.py
        ▼  build.py   →  data/gdelt_*_daily.csv（日次ストア・28日ベースライン）, data/vintages/（制裁リストの日次差分）, web/data.json
        ▼  web/       →  世界地図（塗り分け・円・矢印・ブロック）・重要事象リスト・相互作用マトリクス・予測市場・制裁・時系列・選挙・出所台帳
```

## 使い方

```bat
pip install -r requirements.txt
python build.py            # 全ソース取得 → web/data.json（初回 約5分：GDELT 28日分の1時間サンプル＋直近2日の全スロット。以降は差分のみ）
serve.bat                  # http://localhost:8766 で表示
python build_dist.py       # dist/index.html（単一ファイル。Artifact や配布用）
run_daily.bat              # 日次更新（タスクスケジューラに登録。コメント参照）
python build.py --only markets,sanctions   # 一部だけ更新
```

BCGのSSLプロキシ配下では `truststore` が Windows 証明書ストアを使う。ACLED・UCDP はAPIトークンが必要なため使っていない
（紛争の実態は GDELT の CAMEO 18〜20 と、Goldstein 強度・実質的対立の比率で代替）。

## 二つの時計

* **事象時計（速いが部分的）**：GDELT（15分）、Polymarket の確率とその1日・7日変化、OFAC の新規指定（前回 vintage との差分）と最近のアクション、
  EU・国連リストの直近追加、国連安保理の会合報道。
* **構造時計（遅いが根本的）**：軍事費・兵員（SIPRI 由来、世銀）、政治的安定・発言力と説明責任・法の支配（世銀 WGI）、人口・GDP、
  同盟・ブロック（NATO・EU・QUAD・AUKUS・BRICS+・SCO・CSTO・ASEAN・GCC・核保有国・安保理P5・OPEC・露朝イラン白の条約網）、選挙カレンダー。

## 重みづけ（透明性のため画面に内訳を表示）

1. **政治的事象の抽出**：異なる国の主体間の事象、または主体タイプが GOV/MIL/REB/OPP/LEG/JUD/COP/SPY/UAF/INS/SEP/IGO/ELI、
   または行為の根コードが 14 抗議・15 軍事態勢・16 関係縮小・17 強制・20 大量暴力。米国ローカルニュース（事件・天候・スポーツ）を除く。
2. **事象スコア** ＝ 媒体数（NumSources：転載に頑健な注目度）× 強度（1＋|Goldstein|/5）× 主体の規模（1＋0.5×関与国の勢力指数の和）× 国際性（1.5）。
   勢力指数 ＝ GDP と軍事費の対最大国比の平均（米国＝1）。台湾は世銀に無いため手動値。
3. **国スコア** ＝ 24時間の事象スコア合計 × 新規性（記事数の28日中央値比 r に対し 1＋0.5·log2 r、上限2.5）。
4. **二国間（dyad）** ＝ 主体国→対象国ごとに協調（QuadClass 1・2）と対立（3・4）のスコアを別々に合計。矩陣は純協調＝協調−対立。
5. 過去分は毎時1ファイルのサンプルを96スロット換算（×4）しているため、直近2日（全スロット）より粗い。

## データモデル

| ファイル | 内容 |
|---|---|
| data/gdelt_country_daily.csv | date × iso3：files（取込スロット数）, articles, sources, events, q1〜q4（QuadClass別記事数）, gold_w（Goldstein×記事数）, score |
| data/gdelt_dyad_daily.csv | date × 主体国 × 対象国：articles, events, coop, conf, gold_w, score（日次上位600ペア） |
| data/gdelt_global_daily.csv | 世界合計 |
| data/vintages/ofac_YYYY-MM-DD.json | OFAC SDN の全エントリ（ent_num → 名称・種別・プログラム）。翌日以降の差分に使う |
| web/data.json | 表示用。国・24h集計・上位400事象・dyad・日次系列・ベースライン・市場・制裁・構造・出所 |

## 既知の制約と次の候補

* GDELT は英語圏メディアに偏り、同一事象が複数行に分かれる。件数は「注目度」として読む。見出しは URL から復元した推定。
* GDELT の主体国コード（CAMEO）は ISO3 とほぼ同じだが、EU・国際機関など国でない主体は dyad に現れない。
* 予測市場は米国居住者が取引できないため参加者に偏りがあり、薄い市場の価格は粗い。
* 候補：ACLED/UCDP（要トークン）で武力紛争の死者数、GDELT GKG のテーマ（THEMES）で「核・海峡・制裁・選挙」などの主題別マップ、
  国連総会投票（UN Digital Library）で投票類似度＝「同盟の実態」、SIPRI 武器移転、Global Trade Alert（要トークン）の貿易措置。
