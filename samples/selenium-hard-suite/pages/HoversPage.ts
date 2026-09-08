import { By, WebDriver, until } from "selenium-webdriver";

const AVATARS = By.css(".figure img");
const CAPTION_NAMES = By.css(".figure .figcaption h5");
const PROFILE_LINKS = By.css(".figure .figcaption a");

export class HoversPage {
  constructor(private readonly driver: WebDriver) {}

  async open(): Promise<void> {
    await this.driver.get("https://the-internet.herokuapp.com/hovers");
  }

  /** The caption is CSS-hidden until the pointer is physically over the avatar. */
  async hoverOverAvatar(index: number): Promise<void> {
    const avatars = await this.driver.findElements(AVATARS);
    await this.driver.actions({ bridge: true }).move({ origin: avatars[index] }).perform();
  }

  async getCaptionName(index: number): Promise<string> {
    const captions = await this.driver.findElements(CAPTION_NAMES);
    await this.driver.wait(until.elementIsVisible(captions[index]), 10000);
    return captions[index].getText();
  }

  async isProfileLinkVisible(index: number): Promise<boolean> {
    const links = await this.driver.findElements(PROFILE_LINKS);
    return links[index].isDisplayed();
  }
}
