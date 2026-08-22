import SwiftUI

@main
struct CoachHealthSyncApp: App {
    @StateObject private var viewModel = SyncViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView(viewModel: viewModel)
        }
    }
}
