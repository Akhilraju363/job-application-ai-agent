# UI Design System

The visual source of truth for the dashboard (`web/`). Every token below exists as a CSS custom
property in `web/css/app.css` (`:root` for light, `:root[data-theme="dark"]` for dark). New UI uses
these tokens — never raw hex values, pixel sizes or ad-hoc shadows.

## Principles

1. **Quiet by default.** Neutral surfaces, one accent colour, semantic colour only where it carries
   meaning (status, validation, score). No decorative gradients, no emoji in UI chrome.
2. **Information-dense, not cluttered.** Compact rows, clear hierarchy, generous line-height for
   reading; whitespace separates groups, borders separate surfaces.
3. **State is never colour-only.** Every status has text (and usually a dot or icon) as well as colour.
4. **Trust over flash.** The product edits a real person's career record; UI copy is precise and
   never implies something happened that didn't (no fake counts, no fake notifications).

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f6f7f9` | `#0b0d12` | App background |
| `--surface` | `#ffffff` | `#12151c` | Cards, sidebar, topbar, dialogs |
| `--surface-2` | `#f3f4f6` | `#171b23` | Subtle fills: table headers, list items, hover |
| `--surface-3` | `#eceef2` | `#1e2330` | Pressed / selected fills |
| `--border` | `#e4e7ec` | `#252b37` | Default 1px borders |
| `--border-strong` | `#cfd4dc` | `#363d4c` | Input borders, hover borders |
| `--text` | `#101828` | `#e7e9ef` | Primary text |
| `--text-2` | `#475467` | `#b3b9c5` | Secondary text, nav items |
| `--muted` | `#667085` | `#8a92a3` | Captions, metadata, placeholders |
| `--primary` | `#2554d8` | `#7090ff` | The single accent: primary buttons, links, focus, active nav |
| `--primary-hover` | `#1d44b8` | `#8ea6ff` | |
| `--primary-soft` | `#eef2fe` | `#1a2140` | Accent tint: active nav, info notices, chips |

Semantic colours (the text colour on its `-soft` tint meets WCAG AA 4.5:1 in both themes):

| Meaning | Token | Light | Dark |
|---|---|---|---|
| Success / verified / Offer | `--green` / `--green-soft` | `#15803d` / `#ecfdf3` | `#4ade80` / `#10291b` |
| Warning / unsaved / Interviewing | `--orange` / `--orange-soft` | `#b45309` / `#fff6ea` | `#fbbf24` / `#2d2210` |
| Danger / error / Rejected | `--red` / `--red-soft` | `#c0262d` / `#fef1f1` | `#f87171` / `#331719` |
| AI / Tailored | `--purple` / `--purple-soft` | `#6d28d9` / `#f4f0ff` | `#b09cff` / `#221b3b` |
| Neutral / Not Applied, Below cutoff | `--grey` | `#98a2b3` | `#5d6576` |

Charts use the chart series classes (`.bar-found` … `.seg-*`) built on these tokens, always with labels or a legend.

## Typography

Font stack — local fonts only (the CSP and `tests/test_frontend_same_origin.py` forbid web-font requests):
`--font: "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`.
Monospace: `--font-mono: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace`.

| Role | Token | Size / line-height | Weight | Notes |
|---|---|---|---|---|
| Display | `--fs-display` | 28 / 34 | 650 | Login title only |
| Page title | `--fs-page` | 22 / 28 | 650 | The page's single `h1`, `letter-spacing: -.015em` |
| Section title | `--fs-section` | 16 / 24 | 600 | `h2` inside a page / card group |
| Card title | `--fs-card` | 14 / 20 | 600 | Card headers |
| Body | `--fs-body` | 14 / 20 | 400 | Default |
| Secondary | `--fs-secondary` | 13 / 18 | 400 | Descriptions, table cells |
| Caption | `--fs-caption` | 12 / 16 | 500 | Hints, badges |
| Metadata | `--fs-meta` | 11 / 14 | 600 | Uppercase, `letter-spacing: .06em` (nav group labels, table headers) |

Numbers in tables and metrics use `font-variant-numeric: tabular-nums`. Resume previews use their own
export-matching fonts (see `UI_MASTER_RESUME.md`) and are exempt from this scale.

## Spacing, radius, elevation

- Spacing scale (4px base): `--s-1` 4, `--s-2` 8, `--s-3` 12, `--s-4` 16, `--s-5` 20, `--s-6` 24, `--s-8` 32, `--s-10` 40.
- Radius: `--r-sm` 6 (badges, small controls), `--r-md` 8 (buttons, inputs), `--r-lg` 12 (cards), `--r-xl` 16 (dialogs), `--r-pill` 999.
- Borders: 1px `--border`; `--border-strong` for inputs and hover. No decorative 2px borders.
- Shadows: `--shadow-xs` (cards), `--shadow-sm` (raised / sticky), `--shadow-lg` (menus, dialogs, drawers).
  Cards rely on the border first; the shadow is a whisper.

## Motion

`--dur-fast` 120ms (hover, press), `--dur` 180ms (menus, toasts, page fade), `--dur-slow` 240ms (drawers),
`--ease` `cubic-bezier(.2,.8,.2,1)`. All transitions and entrance animations are disabled under
`prefers-reduced-motion: reduce`. Motion only communicates change; nothing loops except loading indicators.

## Component visuals (behaviour lives in `UI_COMPONENTS.md`)

- **Cards** `.card`: `--surface`, 1px border, `--r-lg`, `--shadow-xs`, padding `--s-5`. Header row `.card-head`.
- **Buttons** `.btn` height 36 (`.btn-sm` 30, `.btn-lg` 42), `--r-md`, weight 600. Variants: `.btn-primary`
  (at most one per region), `.btn-outline` (secondary), `.btn-soft` (tinted tertiary), `.btn-green` (positive
  commit, e.g. Save to Tracker), `.btn-ghost` (toolbar), `.btn-icon` (34×34, icon-only, needs `aria-label`).
  Disabled: 50% opacity. Busy: inline spinner + disabled (`withBusy`).
- **Inputs / selects / textareas** `.input`: 36px, `--r-md`, `--border-strong`; focus = `--primary` border + 3px
  `--primary-soft` ring. Error = `--red` border + `.field-errors` message below.
- **Dropdown menu** `.menu`: `--surface`, `--r-lg`, `--shadow-lg`, items 32px.
- **Badges** `.badge` pill, 12px/600, tones `neutral | blue | green | orange | red | purple`; `.badge-dot` adds a leading dot.
- **Status indicators** `statusBadge(status)` (tones from `lib/format.js statusTone`, pinned by `web/tests/lib.test.mjs`):
  Not Applied → neutral, Applied → blue, Interviewing → orange, Offer → green, Rejected → red. Always dot + text.
- **Score badges** `matchBadge(pct)` / `scoreBadge(score)`: ≥80 green, ≥60 orange, else red; always print the number.
- **Tooltips**: native `title` (icon-only buttons, collapsed sidebar). No custom tooltip layer.
- **Dialogs** `.dialog` (`--r-xl`, max 520px) and **drawers** `.drawer` (right, max 560px), both `--shadow-lg` over a dimmed overlay.
- **Tables** `.table`: 13px, 44px rows, header row `--surface-2` with metadata-style labels, row hover `--surface-2`,
  first column identifies the row (bold). Always inside `.table-wrap` (horizontal scroll on small screens).
- **Tabs** `.tabs/.tab`: segmented control on `--surface-2`; the active tab is a raised `--surface` pill.
- **Pagination**: “Showing X–Y” + Newer/Older or Previous/Next (`.btn-outline.btn-sm`).
- **Toasts** `.toast`: bottom-right, `--r-md`, `--shadow-lg`, success/error tones; 4.5s (errors 9s); slide-in.
- **Loading**: `skeleton('lines'|'table'|'chart')` for sections; `spinner` inside buttons.
- **Empty state** `.state-empty`: icon tile, title, one sentence, at most one real action.
- **Error state** `.state-error`: plain message + Retry; never a stack trace.
- **Success**: toast for transient success; `badge-green` for persistent success (“Verified”).
- **AI processing**: `stageList` — the server task's real stages (pending / active spinner / done check / error) + one honest
  sentence about duration. Never a fake percentage.
