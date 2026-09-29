# Accessibility (WCAG 2.2 AA-oriented)

## Structure
- Landmarks: `aside` sidebar (`aria-label="Primary"`) containing `nav`, `header` topbar, `main#main`.
- Focus: after an in-app navigation focus moves to `main#main`; on first load it stays at the top of the document so
  the first Tab reaches “Skip to content”, then the sidebar.
- A “Skip to content” link is the first focusable element.
- One `h1` per page (PageHeader); headings nest hierarchically inside cards.
- The active nav link has `aria-current="page"`; `document.title` = “{Page} · Job Application AI Agent”.

## Keyboard
- Everything works with Tab / Shift-Tab / Enter / Space / Escape; no hover-only actions.
- Visible focus: `:focus-visible` 2px `--primary` outline with offset on every interactive element, including table
  rows that open drawers.
- Dialogs / drawers: focus moves in, Escape closes, focus returns to the trigger. The mobile nav drawer closes on Escape.

## Forms
- Every control has a visible `<label>` (or `aria-label` for icon-only / inline table controls).
- Errors: text next to the field, `aria-invalid="true"`, `role="alert"` on the error list; failed submits also toast.

## Colour & contrast
- Text ≥ 4.5:1 and UI components ≥ 3:1 in both themes, including semantic text on its soft tint.
- State is never colour-only: badges carry text; status badges add a dot; charts have labels / legends.

## Live regions
- Toast host `role="status" aria-live="polite"`; the AI processing panel `aria-live="polite"`; loading sections `aria-busy`.

## Tables
- `<th scope="col">`; the first column identifies the row; row actions carry the row title in their label.

## Motion
- `prefers-reduced-motion: reduce` disables transitions and entrance animations and slows loaders.
