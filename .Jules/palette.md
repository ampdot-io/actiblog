# Palette's Journal - Critical Learnings

## 2024-05-22 - Initial Entry
**Learning:** Starting fresh.
**Action:** Will document critical UX/a11y learnings as I discover them.

## 2025-02-27 - Skip Link Implementation
**Learning:** The 'Skip to main content' pattern relies on both the link and the target ID. In Hugo themes, the target ID (`<main>`) is often buried in individual layouts rather than a shared base template.
**Action:** Always check all layout files (`_default`, `posts`, `index`) to ensure the `id="main-content"` is applied consistently across all page types.
