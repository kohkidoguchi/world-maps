# World Maps

Automatically built world maps, published to GitHub Pages and embedded in the news digest e-mail.

| Map | Source | Schedule |
|---|---|---|
| `geo/`  — politics & geopolitics | GDELT 2.0, Polymarket, OFAC/EU/UN sanctions, World Bank | Mon & Thu 03:00 JST |
| `corp/` — corporate activity     | Google News RSS (9 languages) → Claude extraction   | Mon & Thu 03:00 JST |

Each run writes `out/<map>/index.html` (interactive D3), `map.png` (screenshot) and `summary.json`
(top items for the e-mail), then deploys `out/` to Pages. Data caches live in Actions cache, not git.
Pushes only build when the commit message contains `[build]`.
