import Foundation

@MainActor final class TradingModel: ObservableObject {
    @Published var state:AppState?; @Published var token=""; @Published var error=""; @Published var connected=false
    private let api=APIClient(); private var poll:Task<Void,Never>?
    func connect(){ poll?.cancel(); poll=Task { while !Task.isCancelled { await refresh(); try? await Task.sleep(for:.seconds(3)) } } }
    func refresh() async { do { let s:AppState=try await api.request("api/state",token:token); state=s; connected=true; error="" } catch { connected=false; self.error="Connection failed" } }
    func control(_ action:String) async { do { let data=try JSONSerialization.data(withJSONObject:["action":action]); let _:ControlReply=try await api.request("api/control",token:token,method:"POST",body:data); await refresh() } catch { self.error="Control failed" } }
}
