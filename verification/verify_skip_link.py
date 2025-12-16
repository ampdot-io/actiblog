from playwright.sync_api import sync_playwright, expect

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:1313")

        # Check if skip link exists
        skip_link = page.locator(".skip-link")
        expect(skip_link).to_have_attribute("href", "#main-content")

        # Take a screenshot before focus (should be hidden)
        # Note: In our CSS it is top: -9999px, so it won't be in viewport, but we can screenshot the top area
        page.screenshot(path="verification/before_focus.png")

        # Focus the link
        skip_link.focus()

        # Take a screenshot after focus (should be visible)
        page.screenshot(path="verification/after_focus.png")

        # Verify it is visible (in viewport)
        # We can check bounding box or computed style if needed, but screenshot is best.

        browser.close()

if __name__ == "__main__":
    run()
