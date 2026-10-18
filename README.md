# Xfinity for Home Assistant

Unofficial Home Assistant integration for an Xfinity residential account. It polls outages, internet usage, and the current bill. It does not open a browser on Home Assistant.

Xfinity and Comcast are trademarks of their owners. This project is not affiliated with, endorsed by, or supported by Comcast.

Requires Home Assistant 2026.3 or newer.

## Install with HACS

1. In HACS, choose Integrations, then the menu, then Custom repositories.
2. Add `https://github.com/andrewtryder/ha-xfinity` as an Integration.
3. Download Xfinity and restart Home Assistant.
4. Go to Settings, Devices & services, Add Integration, and choose Xfinity.

You can also copy `custom_components/xfinity` into Home Assistant's `custom_components` directory and restart.

## Create a refresh token

Home Assistant cannot sign in to Xfinity. Akamai Bot Manager blocks a headless browser, and Home Assistant does not have the packages that desktop login needs. Create the token once on a computer that can show a window:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python get_token.py
```

The script asks for your Xfinity username and password in the terminal. Typing is hidden, and the password is not saved. A browser window opens. If Xfinity asks for a verification code, enter it in that window. When sign-in finishes, the terminal prints one refresh token. Copy that line into the integration, along with the service address used for outage checks.

The script does not write the token or cookies to disk. Home Assistant stores the refresh token in the config entry and replaces it when Xfinity rotates it. Do not share the token. A cookie from xfinity.com is not this token.

The Android application's OAuth client id and client secret are included in this project. They are the same public credentials shipped in the official app. You do not paste them.

## Entities

Sensors:

- Data used, data allowance, and percent of allowance used
- Usage cycle end and data policy
- Outage status
- Bill status, amount due, bill due date, and past-due amount

Binary sensors:

- Active outage
- Scheduled maintenance
- Past due bill

Outages, including scheduled maintenance, do not need a token. Usage and bill status do. Outages refresh every 30 minutes. Usage and the bill refresh every six hours. A failure in one of those does not clear the last good value of the others.

## Troubleshooting

If Xfinity rejects the refresh token, Home Assistant opens a reauthentication form. Run `python get_token.py` again and paste the new token. The service address can be changed with Configure on the integration without creating a new token.

If Xfinity rejects this integration's application credential, Home Assistant raises a repair that asks you to update the integration. A new refresh token will not fix that. It means Xfinity changed the credential shipped in the official app.

## Privacy

The integration stores the service address and the refresh token. Access tokens stay in memory. The password is never stored. Diagnostics omit the address and every token. Bill data does not include the account number.

## Command-line probe

`xfinity_api_probe.py` is the standalone client this integration was built from. `get_token.py` is the supported way to create a Home Assistant token. The probe can also check an outage without an account:

```sh
export XFINITY_SERVICE_ADDRESS='Your street address, city, state ZIP'
python xfinity_api_probe.py --outage-only
```

Other login methods (`http`, `manual`, `chrome`) are development tools. They are not used by Home Assistant.
