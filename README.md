# FairGrow for Home Assistant

Share the sensors you already have in Home Assistant with [FairGrow](https://fairgrow.eu),
the platform for growers. Temperature in the greenhouse, soil moisture in the field, light,
humidity, power: if Home Assistant sees it, FairGrow can use it for your dashboard, your
fields and your automations.

- **Nothing extra to install.** No second computer, no bridge, no USB stick. Only Home Assistant.
- **Any integration.** ZHA, Zigbee2MQTT, Shelly, ESPHome, a weather station: it does not matter
  how a sensor got into Home Assistant.
- **You choose.** FairGrow only sees what you tick, and only switches the lights and switches
  you allow. Nothing else leaves your home.
- **Nothing lost.** If your internet drops, Home Assistant keeps the readings and sends them later.

## What you need

- Home Assistant 2025.1 or newer.
- On Home Assistant 2026.11 or newer nothing else: the built-in **Marketplace** installs FairGrow.
  On older versions you need [HACS](https://www.hacs.xyz/) (the Home Assistant Community Store).
  Not installed yet? Follow the steps on <https://www.hacs.xyz/docs/use/download/download/>.
- A FairGrow account on <https://fairgrow.eu>.

## Install, step by step

### 1. Add FairGrow to Home Assistant

The quickest way is this button. It opens your own Home Assistant with FairGrow filled in: the
Marketplace on 2026.11 or newer, HACS on older versions.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=EasyDataRob&repository=fairgrow-ha&category=integration)

Or by hand on **Home Assistant 2026.11 or newer** (Marketplace):

1. Go to **Settings**, **Marketplace**. The first time, confirm the notice about third-party code.
2. Choose **Add from link** and paste `https://github.com/EasyDataRob/fairgrow-ha`.
3. Home Assistant asks you to connect GitHub: it shows a code, you enter that code on
   github.com and approve the request. Home Assistant only reads public information with it.
4. FairGrow is now in the list. Open the three dots next to it and choose **Install**.
   No restart needed: Home Assistant goes straight on to the pairing code of step 3, so first
   get that code (step 2).

Or by hand on **older versions** (HACS):

1. In Home Assistant, open **HACS** in the side bar.
2. Click the **three dots** at the top right and choose **Custom repositories**.
3. In the field **Repository**, paste `https://github.com/EasyDataRob/fairgrow-ha`.
4. As **Type**, choose **Integration**. Click **Add**, then close the window.
5. Search for **FairGrow** in HACS, open it and click **Download**. Confirm with **Download**.
6. Restart Home Assistant: **Settings**, then the three dots at the top right, **Restart Home
   Assistant**.

### 2. Get your pairing code on FairGrow

1. Log in on <https://fairgrow.eu>.
2. Open **Sensors** in the top bar, choose **Add Zigbee coordinator** and pick **Home Assistant**
   at the top of the list. (The same button is on the knowledge base page
   [Connect your own Zigbee coordinator](https://fairgrow.eu/connect-your-own-zigbee-coordinator/).)
3. Click **Create pairing code**. You get eight characters, for example `7K4M-2QP9`.
   The code works once and lasts fifteen minutes.

### 3. Paste the code in Home Assistant

Installed through the Marketplace? Then Home Assistant already asks for the code; paste it and
click **Submit**. Otherwise:

1. In Home Assistant, go to **Settings**, **Devices & services**.
2. Click **Add integration** at the bottom right and search for **FairGrow**.
3. Paste the pairing code and click **Submit**.

### 4. Choose what you share

The form has four parts. Everything is off until you tick it.

- **Sensors, lights and switches to share**: what FairGrow may see. For a light or switch that is
  only whether it is on or off.
- **Lights and switches FairGrow may switch**: what an automation on fairgrow.eu may turn on, off
  or toggle. Leave it empty and FairGrow switches nothing.
- **Share automations.yaml** and **Share scripts.yaml**: read only, so that you can take your
  automations and scripts over on fairgrow.eu. Each file has its own box.

Click **Submit**. Done. On your FairGrow dashboard the
sensors show up in the card named **Home Assistant** followed by the name of your installation
(for example *Home Assistant Home*), in the Devices section. A sensor shows its first value as soon
as it reports one; some sensors only report when the value changes.

To change your choice later: **Settings**, **Devices & services**, **FairGrow**, **Configure**.

## What is sent to FairGrow

For each sensor you tick, and nothing else:

| What | Why |
|---|---|
| The entity id, its name, its device class and unit | So FairGrow knows it is, say, a temperature in °C |
| The name, area, maker and model of the device it belongs to | So your readings land on one card per device, with a name you recognise |
| For Zigbee devices: the Zigbee address | Only so that a device you connected earlier through the FairGrow bridge keeps its history |
| The state and the time it changed | The reading itself |

If you tick **Share automations.yaml** or **Share scripts.yaml**, that file is read (read only)
and its automations or scripts are sent, so that you can take them over on fairgrow.eu. `!secret`
and `!include` stay as text: FairGrow sees `!secret wifi_password`, never the password. No other
file is read. Taking one over makes a copy on FairGrow that starts switched off; your automation
in Home Assistant stays as it is.

Your Home Assistant address, your login, your other devices and your network key never leave your
home.

## Letting FairGrow switch lights and switches

Only the lights and switches on the list **Lights and switches FairGrow may switch** can be
switched, and only on, off and toggle. That list lives in Home Assistant: nobody can extend it from
fairgrow.eu. Every ten seconds the integration asks FairGrow whether there is something to switch,
runs it and reports back. A command that arrives more than two minutes late is refused, because a
lamp that switches on five minutes late is worse than one that does not switch at all.

Example: share a temperature sensor and a light strip, allow FairGrow to switch the strip, make the
automation "above 20 degrees: light on" in Home Assistant, take it over on fairgrow.eu and switch
it on there. When the room warms up, FairGrow turns the strip on.

## Stop sharing

- Remove the integration in Home Assistant (**Settings**, **Devices & services**, **FairGrow**,
  three dots, **Delete**). This also disconnects it on FairGrow.
- Or disconnect it on FairGrow under **Your coordinators**. Home Assistant will then ask you for a
  new pairing code if you want to continue.

Your history on FairGrow stays in both cases.

## Problems?

- **"FairGrow does not know this code"**: check the eight characters, or create a new code.
- **"This code was made for the FairGrow bridge"**: under Add Zigbee coordinator, pick
  **Home Assistant** at the top of the list before you make the code, not ZHA or Zigbee2MQTT
  (those are for the separate bridge).
- **A sensor does not appear on FairGrow**: FairGrow uses sensors with a device class it knows.
  The list of skipped sensors and the reason is in the diagnostics: **Settings**, **Devices &
  services**, **FairGrow**, three dots, **Download diagnostics**. The diagnostics never contain
  your token. Open an [issue](https://github.com/EasyDataRob/fairgrow-ha/issues) or send that file to us via
  <https://fairgrow.eu/contact/>.

## For developers

```
pip install -r requirements_test.txt
pytest                      # config flow, pusher, diagnostics, with a mocked FairGrow
pytest --live tests/test_live.py   # against the real fairgrow.eu, needs two accounts:
                            # FG_LIVE_A_USER/FG_LIVE_A_PW and FG_LIVE_B_USER/FG_LIVE_B_PW
```

Home Assistant does not run on Windows; run the tests on Linux or macOS (or in CI).

The platform side lives on fairgrow.eu under `/wp-json/fg-ha/v1/` (pair, entities, states,
heartbeat, unpair). Readings use the same storage as every other FairGrow sensor.

## License

MIT, see [LICENSE](LICENSE).
