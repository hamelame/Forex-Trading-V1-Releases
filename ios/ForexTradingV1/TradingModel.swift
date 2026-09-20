import Foundation

@MainActor final class TradingModel: ObservableObject {
    @Published var state:AppState?; @Published var token=""; @Published var error=""; @Published var connected=false
    @Published var busy=false; @Published var transientError=false
    private let api=APIClient(); private var poll:Task<Void,Never>?

    func connect(){ poll?.cancel(); poll=Task { while !Task.isCancelled { await refresh(); try? await Task.sleep(for:.seconds(3)) } } }

    func refresh() async {
        do {
            let s:AppState=try await api.request("api/state",token:token)
            state=s; connected=true; transientError=false; error=""
        } catch APIError.unauthorized {
            connected=false; transientError=false; error="Wrong mobile access token"; poll?.cancel()
        } catch {
            // Never throw the user back to login for a sleeping Render instance,
            // timeout, temporary network loss or a single malformed refresh.
            transientError=true
            error=state == nil ? "Server is waking up — retrying…" : "Connection interrupted — keeping last live state"
            if state != nil { connected=true }
        }
    }

    func control(_ action:String, extras:[String:Any]=[:]) async {
        busy=true; defer{busy=false}
        do {
            var payload:[String:Any]=["action":action]; extras.forEach{payload[$0.key]=$0.value}
            let data=try JSONSerialization.data(withJSONObject:payload)
            let _:ControlReply=try await api.request("api/control",token:token,method:"POST",body:data)
            error=""; await refresh()
        } catch APIError.unauthorized {
            connected=false; error="Wrong mobile access token"
        } catch {
            error="Control request failed — current state was kept"
        }
    }
    func newSession(capital:Double) async { await control("new_session",extras:["capital":capital]) }
    func setTopN(_ n:Int) async { await control("set_top_n",extras:["value":n]) }
    func selectMarket(_ symbol:String) async { await control("select_market",extras:["symbol":symbol]) }
}
