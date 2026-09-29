# Responsive behaviour

| Breakpoint | Shell | Content |
|---|---|---|
| ≥1280 desktop | Full sidebar (248px) with group labels | Multi-column grids, full tables |
| 1100–1280 | Full sidebar | Dashboard middle row 2 columns; Master Resume section nav hidden |
| 760–1100 tablet | Collapsed icon sidebar (72px); labels via `title` tooltips | KPIs 2×2, two-column forms collapse, tables scroll inside their card |
| <760 mobile | Sidebar becomes an off-canvas drawer (menu button in the topbar, scrim, Escape closes) | Single column, stacked cards, full-width primary actions, tables scroll horizontally inside `.table-wrap` |

Rules
- **No horizontal page overflow** at any width (checked at 390px). Only `.table-wrap` and `.board` scroll horizontally.
- Touch targets ≥ 34px with spacing; form controls use 16px text on mobile (prevents iOS zoom).
- Sticky elements (topbar, Master Resume nav / preview / save bar) become static where they would cover content.
- Verify at 390×844, 768×1024, 1280×800 and 1600×1000, in both themes.
