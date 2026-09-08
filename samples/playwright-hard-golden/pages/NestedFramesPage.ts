import { type Locator, type Page } from "@playwright/test";

/**
 * frameLocator() scopes a lookup instead of moving the page into a frame, so
 * nothing here is stateful: the enterTopFrame()/returnToTop() pair from the
 * Selenium page object has nothing left to do and is removed. Reads may now
 * happen in any order.
 */
export class NestedFramesPage {
  readonly middleContent: Locator;
  readonly bottomBody: Locator;

  constructor(private readonly page: Page) {
    this.middleContent = page
      .frameLocator("frame[name='frame-top']")
      .frameLocator("frame[name='frame-middle']")
      .locator("#content");
    this.bottomBody = page.frameLocator("frame[name='frame-bottom']").locator("body");
  }

  async open(): Promise<void> {
    await this.page.goto("/nested_frames");
  }
}
