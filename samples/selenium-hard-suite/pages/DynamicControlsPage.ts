import { By, until } from "selenium-webdriver";
import { BasePage } from "./BasePage";

const CHECKBOX = By.css("#checkbox-example input[type='checkbox']");
const CHECKBOX_TOGGLE = By.css("#checkbox-example button");
const MESSAGE = By.id("message");
const TEXT_FIELD = By.css("#input-example input");
const ENABLE_TOGGLE = By.css("#input-example button");

export class DynamicControlsPage extends BasePage {
  async open(): Promise<void> {
    // An implicit wait applies to EVERY findElement/findElements from here on,
    // including the absence check below — which therefore takes five seconds
    // to conclude "not there" rather than answering immediately.
    await this.driver.manage().setTimeouts({ implicit: 5000 });
    await this.visit("/dynamic_controls");
  }

  async removeCheckbox(): Promise<void> {
    const checkbox = await this.driver.findElement(CHECKBOX);
    await this.waitAndClick(CHECKBOX_TOGGLE);
    // The old node is detached, not hidden: hold the reference and wait for it
    // to go stale, because a fresh lookup would simply find nothing.
    await this.driver.wait(until.stalenessOf(checkbox), BasePage.TIMEOUT);
  }

  async addCheckbox(): Promise<void> {
    await this.waitAndClick(CHECKBOX_TOGGLE);
    await this.driver.wait(until.elementLocated(CHECKBOX), BasePage.TIMEOUT);
  }

  /** Presence as a number, backed by the implicit wait configured in open(). */
  async isCheckboxPresent(): Promise<boolean> {
    const found = await this.findAll(CHECKBOX);
    return found.length > 0;
  }

  async getMessage(): Promise<string> {
    return this.waitForText(MESSAGE);
  }

  async enableTextField(): Promise<void> {
    await this.waitAndClick(ENABLE_TOGGLE);
    // until has no "becomes enabled" condition for an element that is replaced,
    // so the suite polls the property it actually cares about.
    await this.waitUntil(async () => {
      const field = await this.driver.findElement(TEXT_FIELD);
      return field.isEnabled();
    }, "the text field never became enabled");
  }

  async typeIntoTextField(text: string): Promise<string> {
    const field = await this.driver.findElement(TEXT_FIELD);
    await field.clear();
    await field.sendKeys(text);
    // getAttribute is typed string | null; an input always reports a value.
    return (await field.getAttribute("value")) ?? "";
  }
}
