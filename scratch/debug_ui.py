from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on('console', lambda msg: print(f"CONSOLE: {msg.type}: {msg.text}"))
        page.on('pageerror', lambda exc: print(f"ERROR: {exc}"))
        
        print("Navigating to http://localhost:10911/rules...")
        page.goto('http://localhost:10911/rules')
        page.wait_for_timeout(3000)
        browser.close()

if __name__ == "__main__":
    main()
