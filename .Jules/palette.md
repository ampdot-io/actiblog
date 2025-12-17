## 2024-05-23 - Accessibility Patterns in Hugo
**Learning:** Hugo layouts often define structure in `baseof.html` or `header.html`, making global a11y fixes (like skip links) efficient but potentially tricky if layouts diverge (e.g. `single.html` vs `index.html` structure).
**Action:** When adding skip links, ensure the target `id="main-content"` wraps the *logical* beginning of content, including page titles (H1), even if the theme splits them from the body text.
