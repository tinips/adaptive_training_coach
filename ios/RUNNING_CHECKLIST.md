# CoachHealthSync iOS App - Running Checklist

## Prerequisites ✓

- [ ] Xcode 15+ installed
- [ ] iOS 15+ device or simulator available
- [ ] Apple Developer account (for team ID: 5DBRG4AT92)
- [ ] Local Coach API running (or ngrok tunnel active)
- [ ] Git repository cloned

## Configuration Setup ✓

- [ ] **Developer.xcconfig file exists** at `ios/CoachHealthSync/Developer.xcconfig`
  - Location: `ios/CoachHealthSync/Developer.xcconfig` (NOT in Config folder)
  - Content: `COACH_API_BASE_URL = https://your-api-url.com`
  - Example: `COACH_API_BASE_URL = https://maturity-founder-exemplary.ngrok-free.dev`

- [ ] **API URL is valid**
  - [ ] Starts with `https://` (HTTP not allowed)
  - [ ] No trailing slashes: `https://example.com` ✓, NOT `https://example.com/`
  - [ ] URL is reachable from your device/network
  - [ ] ngrok tunnel is active (if using ngrok)

- [ ] **Apple Developer Team ID configured**
  - Team ID: `5DBRG4AT92` 
  - Location: Xcode > Build Settings > Code Signing > Development Team
  - Status: Should show ✓ in project.pbxproj

- [ ] **iOS Deployment Target set correctly**
  - Minimum: iOS 15.0
  - Current: iOS 15.0

## Build & Compile ✓

- [ ] Open Xcode: `open ios/CoachHealthSync/CoachHealthSync.xcodeproj`

- [ ] Select target device/simulator
  - [ ] Simulator: iPhone 15 Pro (or any iOS 15+ simulator)
  - [ ] OR Physical device: Connected via USB with trust enabled

- [ ] Clean build folder
  - Command: `Cmd + Shift + K`

- [ ] Build the project
  - Command: `Cmd + B`
  - Expected: ✓ Build Succeeded (no Swift compilation errors)
  - Note: Provisioning profile warning is OK if building for Mac

## Run the App ✓

- [ ] Press **Play (Run)** button or `Cmd + R`
  
- [ ] App launches successfully
  - [ ] No crash on startup
  - [ ] UI displays "Coach Health Sync" navigation title
  - [ ] Form displays with "Connection" section

## Initial Setup Flow ✓

### Step 1: Connect iPhone
- [ ] Telegram bot is running with `/connect_iphone` command available
- [ ] Execute `/connect_iphone` in Telegram
- [ ] Copy the pairing code from bot response
- [ ] Paste code into app's "Pairing code" field
- [ ] Tap "Connect iPhone"
- [ ] Status message shows: "This iPhone is connected"
- [ ] Connection section now shows: 
  - [ ] Green checkmark icon
  - [ ] "This iPhone is connected"
  - [ ] "Reset connection" button
  - [ ] Explanation about disconnecting via Telegram

### Step 2: Authorize Apple Health
- [ ] Apple Health section appears
- [ ] Tap "Authorize Apple Health" button
- [ ] iOS permission popup appears asking for HealthKit access
- [ ] Grant "Read" permissions for health data
- [ ] Status message shows: "Apple Health access requested. Loading recent workouts..."

### Step 3: Load Workouts
- [ ] Status updates to show workout count
- [ ] If workouts found: "Choose one workout, then tap Sync now."
- [ ] If no workouts: "No workouts were found in the last 3 months."
- [ ] Workouts section populates with items (if available)
- [ ] Each workout row shows:
  - [ ] Activity name (running, cycling, etc.)
  - [ ] Date and time
  - [ ] Duration, distance, calories
  - [ ] Source (Apple Watch, iPhone, etc.)

### Step 4: Sync Workouts
- [ ] Select a workout by tapping it
  - [ ] Selected row shows blue checkmark circle
  - [ ] "Sync now" button becomes enabled

- [ ] Tap "Sync now"
  - [ ] Loading spinner appears
  - [ ] Status message shows result: "Workout synchronized: [inserted|updated|unchanged]"

- [ ] OR tap "Sync all"
  - [ ] All workouts sync sequentially
  - [ ] Status shows: "Synced all: X new, Y updated, Z unchanged."

## Error Handling ✓

### If App Crashes on Launch:
- [ ] Check `Developer.xcconfig` exists and has valid URL
- [ ] Verify API URL is reachable: `curl https://your-url.com/health`
- [ ] Check Info.plist for `CoachAPIBaseURL` variable
- [ ] Review Xcode Console for error messages

### If API Connection Fails:
- [ ] Error dialog shows specific message
- [ ] Check network connectivity
- [ ] Verify ngrok tunnel is still active: `ngrok status`
- [ ] Confirm API server is running
- [ ] Check firewall isn't blocking HTTPS

### If HealthKit Authorization Fails:
- [ ] Check app has HealthKit permission in Settings > Privacy > Health
- [ ] Verify device has HealthKit support (not all simulators do)
- [ ] Try on physical device if simulator doesn't work

### If 401 Unauthorized Error:
- [ ] Pairing code may be expired
- [ ] Tap "Reset connection" and pair again
- [ ] Verify bot is generating valid tokens

## Testing Checklist ✓

- [ ] **Offline Mode**: Disconnect from API, verify graceful error
- [ ] **No Workouts**: Clear Health data, verify "No workouts found" message
- [ ] **Multiple Workouts**: Load 10+ workouts, verify they all display
- [ ] **Rotation**: Rotate device, UI adapts correctly
- [ ] **Background**: Send app to background, reopen (should auto-sync)
- [ ] **Reconnection**: Reset connection, pair again successfully
- [ ] **Data Persistence**: Close app, reopen (connection state persists)

## Performance Checklist ✓

- [ ] App launches in < 2 seconds
- [ ] Workout list loads in < 3 seconds
- [ ] Sync completes < 5 seconds per workout
- [ ] No memory warnings in Console
- [ ] No zombie objects in Instruments

## Final Verification ✓

- [ ] Build compiles without errors
- [ ] App runs without crashes
- [ ] Pairing workflow completes
- [ ] HealthKit authorization succeeds
- [ ] At least one workout can be synced
- [ ] Status message confirms sync outcome

## Troubleshooting Quick Reference

| Issue | Solution |
|-------|----------|
| "Set COACH_API_BASE_URL..." error | Create `Developer.xcconfig` with valid HTTPS URL |
| "Provisioning profile" error | Building on Mac is OK; build for iOS device/simulator |
| "Couldn't complete that action" | Check API is running and reachable |
| No workouts appear | Authorize Apple Health, ensure workouts exist in Health app |
| App crashes on launch | Check Xcode Console for detailed error |
| ngrok URL keeps changing | Use ngrok paid plan or update URL after tunnel restart |

## Success Indicators ✓

App is running well when:
- ✅ Launches without crashing
- ✅ Connects to API successfully  
- ✅ Authorizes HealthKit access
- ✅ Loads and displays workouts
- ✅ Syncs workout data to backend
- ✅ Shows appropriate status/error messages
- ✅ Handles reconnection gracefully
- ✅ Persists connection state

---

**Last Updated**: 2026-08-29  
**iOS Target**: 15.0+  
**Team ID**: 5DBRG4AT92
