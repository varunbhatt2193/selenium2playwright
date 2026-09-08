import { By, WebDriver, until } from "selenium-webdriver";

const ADD_BUTTON = By.css("button[onclick='addElement()']");
const DELETE_BUTTONS = By.css("#elements .added-manually");

export class AddRemovePage {
  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get("https://the-internet.herokuapp.com/add_remove_elements/");
  }

  async addElements(count: number): Promise<void> {
    const button = await this.driver.wait(until.elementLocated(ADD_BUTTON), 10000);
    // The list grows downward and pushes the button toward the fold; scroll it
    // back before each click so Selenium's own click lands on the element.
    await this.driver.executeScript("arguments[0].scrollIntoView({block: 'center'})", button);
    for (let index = 0; index < count; index += 1) {
      // A JavaScript click, because a native click intermittently hit the
      // footer overlay once the row list was long enough to reflow the page.
      await this.driver.executeScript("arguments[0].click()", button);
    }
  }

  async countDeleteButtons(): Promise<number> {
    const buttons = await this.driver.findElements(DELETE_BUTTONS);
    return buttons.length;
  }

  async deleteAll(): Promise<void> {
    // findElements hands back a snapshot of nodes. Every click detaches one of
    // its members, so iterating that array once would click stale references:
    // re-query after each removal and always take whatever is first now.
    let remaining = await this.driver.findElements(DELETE_BUTTONS);
    while (remaining.length > 0) {
      await remaining[0].click();
      remaining = await this.driver.findElements(DELETE_BUTTONS);
    }
  }
}
