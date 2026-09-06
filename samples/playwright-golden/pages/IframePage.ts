import { type Locator, type Page } from "@playwright/test";

export class IframePage {
  readonly editorBody: Locator;
  readonly heading: Locator;

  constructor(private readonly page: Page) {
    // FrameLocator resolves the generated frame when used; it does not switch Page state.
    this.editorBody = page.frameLocator(".tox-edit-area iframe").locator("#tinymce");
    this.heading = page.getByRole("heading", { level: 3 });
  }

  async open(): Promise<void> {
    await this.page.goto("/iframe");
  }
}
