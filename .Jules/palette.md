## 2024-05-23 - Skip to Main Content
**Learning:** Adding a "Skip to main content" link requires checking all main layouts (`single.html`, `list.html`, `terms.html`) to ensure the `id="main-content"` target exists everywhere. Overriding theme templates is often necessary to add this ID if the theme doesn't provide it.
**Action:** When auditing for a11y, always trace the template hierarchy to ensure semantic main tags with IDs are present on all page types.
