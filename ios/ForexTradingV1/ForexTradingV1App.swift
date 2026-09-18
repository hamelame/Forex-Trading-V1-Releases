import SwiftUI

@main
struct ForexTradingV1App: App {
    @StateObject private var model = TradingModel()
    var body: some Scene { WindowGroup { RootView().environmentObject(model) } }
}
