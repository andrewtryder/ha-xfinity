# Xfinity for Home Assistant

<img src="custom_components/xfinity/brand/logo.png" alt="Xfinity integration logo" width="180">

See service outages, monthly internet data usage, and current billing details for one Xfinity residential account in Home Assistant.

[![Latest release](https://img.shields.io/github/v/release/andrewtryder/ha-xfinity?style=flat-square)](https://github.com/andrewtryder/ha-xfinity/releases/latest)
[![Validation on main](https://img.shields.io/github/actions/workflow/status/andrewtryder/ha-xfinity/validate.yml?branch=main&style=flat-square&label=validation)](https://github.com/andrewtryder/ha-xfinity/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/andrewtryder/ha-xfinity?style=flat-square)](LICENSE)
[![HACS custom integration](https://img.shields.io/badge/HACS-Custom-orange?style=flat-square)](https://my.home-assistant.io/redirect/hacs_repository/?owner=andrewtryder&repository=ha-xfinity&category=integration)

> [!IMPORTANT]
> This is an unofficial community integration. Xfinity and Comcast are trademarks of their respective owners; this project is not affiliated with, endorsed by, or supported by Comcast or Xfinity.

## What can I do with it?

- Check service outage status, active outage alerts, and scheduled maintenance for your service address every 30 minutes.
- Track monthly internet data used, data allowance, percentage used, usage policy, and billing cycle end date every 6 hours.
- View current bill status, amount due, due date, and past-due alerts every 6 hours.

A failure in one request (outage, usage, or bill) does not clear the last known good value of the others.

## Requirements and compatibility

- Home Assistant **2026.8.0 or newer** and HACS.
- One residential Xfinity account (username, password, and service address).
- A [Browserless](https://www.browserless.io) API key. Each sign-in uses a residential proxy session in Browserless so Home Assistant does not need to run a local browser.

## Install with HACS

1. In HACS, add `https://github.com/andrewtryder/ha-xfinity` as a custom repository in the **Integration** category, or use the HACS badge above.
2. Find and download **Xfinity**.
3. Restart Home Assistant.

For manual installation, copy `custom_components/xfinity` from a release into your Home Assistant configuration's `custom_components` directory and restart Home Assistant.

## First-time setup

1. Go to **Settings → Devices & services → Add integration** and select **Xfinity**.
2. Enter your service address (used for outage checks), Xfinity username, Xfinity password, and Browserless API key.
3. If Xfinity prompts for two-step verification during sign-in, approve the request in the Xfinity app and submit the setup form again.

You can update the service address or Browserless API key later with **Reconfigure** on the integration, or update the Xfinity password using the reauthentication flow.

## What you'll see in Home Assistant

- **Sensors:** data used (GB), data allowance (GB), data used percent, usage cycle end date, data policy (diagnostic), outage status (`no_reported_incident`, `incidents_reported`, or `unknown`), bill status, amount due (USD), bill due date, and past-due amount (USD).
- **Binary sensors:** active outage, scheduled maintenance, and past-due bill.

## Common problems and troubleshooting

- **Xfinity rejected the username or password:** re-enter your Xfinity account username and password in the setup or reauthentication form.
- **Verification code required:** Xfinity requested multi-factor verification. Approve the prompt in the Xfinity app, then run setup or reauthentication again.
- **Browserless error:** confirm the Browserless API key is valid and has available usage units. HTTP 429 indicates the Browserless account is rate-limited.
- **Repair "Xfinity needs an integration update":** Xfinity rejected the bundled mobile app OAuth credential. Update the integration to a newer release; changing your account password will not resolve this.

## Limitations, safety, and privacy

- **Browserless credential handling:** Browserless is a third-party cloud browser service. Your Xfinity username and password are sent to Browserless only when signing in: during initial setup, when you reauthenticate, and at most once per hour if Xfinity rejects the stored refresh token. After sign-in, Home Assistant renews tokens directly with Xfinity.
- **Local storage and diagnostics:** your service address, Xfinity username, Xfinity password, Browserless API key, and refresh token are stored in the Home Assistant config entry; access tokens remain in memory only. Downloaded diagnostics automatically omit tokens, credentials, and the service address.
- **Single account and verification limits:** only one Xfinity account is supported per Home Assistant instance, and verification codes cannot be typed directly inside Home Assistant.
- **Removing the integration:** deleting **Xfinity** from **Settings → Devices & services** removes the device, entities, and locally stored credentials and tokens.

## More documentation

- [Contributing and local validation](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Support and license

Open a [GitHub issue](https://github.com/andrewtryder/ha-xfinity/issues) for reproducible problems, and never include credentials, tokens, or personal billing details. Distributed under the [MIT License](LICENSE).
