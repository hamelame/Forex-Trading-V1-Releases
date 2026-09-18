import Foundation

@MainActor final class TradingModel: ObservableObject {
    @Published var state:AppState?; @Published var token=""; @Published var error=""; @Published var connected=false
    @Published var busy=false
    private let api=APIClient(); private var poll:Task<Void,Never>?
    func connect(){ poll?.cancel(); poll=Task { while !Task.isCancelled { await refresh(); try? await Task.sleep(for:.seconds(3)) } } }
    func refresh() async { do { let s:AppState=try await api.request("api/state",token:token); state=s; connected=true; error="" } catch { connected=false; self.error="Connection failed" } }
    func control(_ action:String, extras:[String:Any]=[:]) async {
        busy=true; defer{busy=false}
        do {
            var payload:[String:Any]=["action":action]; extras.forEach{payload[$0.key]=$0.value}
            let data=try JSONSerialization.data(withJSONObject:payload)
            let _:ControlReply=try await api.request("api/control",token:token,method:"POST",body:data)
            await refresh()
        } catch { self.error="Control failed" }
    }
    func newSession(capital:Double) async { await control("new_session",extras:["capital":capital]) }
    func setTopN(_ n:Int) async { await control("set_top_n",extras:["top_n":n]) }
    func selectMarket(_ symbol:String) async { await control("select_market",extras:["symbol":symbol]) }
}
