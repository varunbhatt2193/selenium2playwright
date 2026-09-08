import { type Locator, type Page } from "@playwright/test";

export class AddRemovePage {
  readonly addButton: Locator;
  readonly deleteButtons: Locator;

  constructor(private readonly page: Page) {
    this.addButton = page.getByRole("button", { name: "Add Element" });
    this.deleteButtons = page.getByRole("button", { name: "Delete" });
  }

  async open(): Promise<void> {
    await this.page.goto("/add_remove_elements/");
  }

  async addElements(count: number): Promise<void> {
    // Both executeScript workarounds are gone: click() scrolls the element into
    // view itself and refuses to fire while something else would receive it,
    // which is exactly what the scrollIntoView and the JS click were faking.
    for (let index = 0; index < count; index += 1) {
      await this.addButton.click();
    }
  }

  async deleteAll(): Promise<void> {
    // A Locator is a query, not a captured node list — but `all()` does snapshot
    // one, and each click here removes a member of it. Re-resolving "the first
    // remaining Delete button" on every pass is what keeps this correct.
    while ((await this.deleteButtons.count()) > 0) {
      await this.deleteButtons.first().click();
    }
  }
}
