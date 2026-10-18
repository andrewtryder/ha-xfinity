# Xfinity for Home Assistant

Unofficial Home Assistant integration for one Xfinity residential account. It polls outages, internet usage, and the current bill.

Xfinity and Comcast are trademarks of their owners. This project is not affiliated with, endorsed by, or supported by Comcast.

Requires Home Assistant 2026.8 or newer.

## Install with HACS

1. In HACS, choose Integrations, then the menu, then Custom repositories.
2. Add `https://github.com/andrewtryder/ha-xfinity` as an Integration.
3. Download Xfinity and restart Home Assistant.
4. Go to Settings, Devices & services, Add Integration, and choose Xfinity.

You can also copy `custom_components/xfinity` into Home Assistant's `custom_components` directory and restart.

To remove it, delete the Xfinity integration from Settings, Devices & services. That removes the device, the entities, and the stored sign-in.

## Setup

You need a [Browserless](https://www.browserless.io) API key. Each sign-in uses a residential proxy session.

The integration asks for:

- Service address, used for outage checks
- Xfinity username
- Xfinity password
- Browserless API key

Home Assistant signs in through Browserless, stores the refresh token, and renews that token itself. It does not open a browser on the Home Assistant machine. If Xfinity asks for a verification code during setup, approve it in the Xfinity app and submit the form again.

The password and Browserless key are stored in the Home Assistant config entry. The password is sent to Browserless only when signing in: during setup, when you reauthenticate, and at most once an hour if Xfinity rejects the refresh token. Access tokens stay in memory.

## What it polls

Outages refresh every 30 minutes and do not need an account. Usage and the bill refresh every six hours. A failure in one request does not clear the last good value of the others.

Sensors: data used, data allowance, percent used, usage cycle end, data policy, outage status, bill status, amount due, bill due date, and past-due amount.

Binary sensors: active outage, scheduled maintenance, and past due bill.

Change the service address or Browserless key with Configure on the integration. Change the password with the reauthentication form.

## Limitations

- One Xfinity account per Home Assistant.
- A verification code cannot be entered inside Home Assistant. Approve it, then try again.
- If Xfinity changes the application credential shipped in its Android app, update this integration. A new password will not fix that, and Home Assistant raises a repair.
- Browserless is a third party. A sign-in sends the username and password to Browserless's cloud browser.

## Troubleshooting

- **Xfinity rejected the username or password.** Re-enter them. The password is the Xfinity account password.
- **Verification code.** Xfinity asked for MFA. Approve the prompt, then run reauthentication.
- **Browserless error.** Check the API key and that the account has units left. HTTP 429 means the account is rate limited.
- **Update the integration.** The repair named "Xfinity needs an integration update" means the bundled app credential was rejected.
