# Coach Health Sync POC

This is a deliberately small iPhone companion app for the Coach backend. It
reads the last three months of **workout summaries only** from Apple Health and
lets the athlete manually synchronize one selected workout.

It does not contain a Watch app, background sync, an offline queue, routes,
GPS, workout notes, granular heart-rate data, or an App Store release flow.

## Monday prerequisites

- A Mac with Xcode 15.4 or newer and an Apple ID configured for code signing.
- A physical iPhone running iOS 17 or newer. HealthKit workout data cannot be
  meaningfully tested in the Simulator.
- The Coach API and Telegram bot running in development.
- `MOBILE_SYNC_ENABLED=true` in the development backend environment, followed by
  an API and bot restart.
- A public HTTPS tunnel pointed at the local API, for example a temporary
  development tunnel. Do not put credentials or tunnel tokens in this repo.
- Mi Fitness configured to write workouts to Apple Health. Confirm a new Xiaomi
  workout appears in the Health app before testing this POC.

## Local Xcode setup

1. Copy `Config/Developer.xcconfig.example` to `Config/Developer.xcconfig`.
2. Replace `COACH_API_BASE_URL` with the public HTTPS base URL of the API, with
   no trailing slash. For example: `https://coach-dev.example`.
3. Open `CoachHealthSync.xcodeproj` in Xcode, select the `CoachHealthSync`
   target, choose your development team, and set a unique bundle identifier if
   Xcode requires one.
4. Connect the physical iPhone, choose it as the run destination, and run the
   app. The committed entitlement already enables the HealthKit capability.

`Developer.xcconfig` is intentionally local-only and must never be committed.
The opaque device token is stored only in the iPhone Keychain; the one-time
pairing code is never stored on the device.

## Manual proof

1. In Telegram, send `/connect_iphone` and copy the one-time pairing code.
2. Open the app, enter the code, and choose **Connect iPhone**.
3. Choose **Authorize Apple Health**, approve workout read access, and refresh
   the list if necessary.
4. Select the Xiaomi workout that is visible in Apple Health and choose
   **Sync now**. The app should report `inserted` (or `updated` if the backend
   already had that HealthKit UUID).
5. Choose **Sync now** again for the same row. The app should report
   `unchanged`; the backend must not create a duplicate workout.
6. Return to Telegram and choose `Plan next week`. The new workout should count
   toward the 30-day evidence gate. A coverage-insufficient message is expected
   until every target discipline reaches three sessions on two active days.

For this POC, do not upload an Apple Health ZIP containing the same session:
the file-import channel uses a different source identity and cross-channel
reconciliation is intentionally a later milestone.
