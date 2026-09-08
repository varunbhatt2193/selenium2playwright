import { type Locator, type Page } from "@playwright/test";

export class HoversPage {
  readonly avatars: Locator;
  readonly captionNames: Locator;
  readonly profileLinks: Locator;

  constructor(private readonly page: Page) {
    this.avatars = page.locator(".figure img");
    this.captionNames = page.locator(".figure .figcaption h5");
    this.profileLinks = page.locator(".figure .figcaption a");
  }

  async open(): Promise<void> {
    await this.page.goto("/hovers");
  }

  /** One built-in action replaces the whole Actions chain. */
  async hoverOverAvatar(index: number): Promise<void> {
    await this.avatars.nth(index).hover();
  }
}
