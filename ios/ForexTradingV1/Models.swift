import Foundation

struct Market: Codable, Identifiable {
 var id:String{symbol}; let symbol:String; let name:String?; let category:String?
 let price:Double?; let bid:Double?; let ask:Double?; let spread:Double?; let rsi:Double?; let quality:Double?
 let session:String?; let feed_status:String?; let feed_provider:String?; let data_age_seconds:Double?
 let action:String?; let score:Double?; let confidence:Double?; let regime:String?; let rank:Double?; let reason:String?; let selected:Bool?
}
struct Position: Codable, Identifiable { var id:String{"\(symbol)-\(side ?? "")"}; let symbol:String; let side:String?; let unrealized:Double?; let pnl:Double?; let reason:String? }
struct Decision: Codable, Identifiable { var id:String{"\(symbol ?? "decision")-\(at ?? ts ?? 0)"}; let symbol:String?; let action:String?; let score:Double?; let confidence:Double?; let regime:String?; let text:String?; let reason:String?; let at:Double?; let ts:Double? }
struct Trade: Codable, Identifiable { var id:String{"\(symbol ?? "")-\(closed_at ?? exit_time ?? 0)"}; let symbol:String?; let side:String?; let pnl:Double?; let reason:String?; let closed_at:Double?; let exit_time:Double? }
struct ShadowSummary: Codable { let samples:Int?; let observations:Int?; let validation_accuracy:Double?; let win_rate:Double?; let pnl:Double?; let shadow_pnl:Double?; let active:Bool?; let source:String? }
struct MarketHealth: Codable { let score:Double?; let regime:String?; let risk:String? }
struct DataQuality: Codable { let average:Double?; let feeds:[String:Int]?; let markets:Int? }
struct SafetyState: Codable { let paper_only:Bool?; let shadow_only:Bool?; let broker_orders:Bool? }
struct AppState: Codable {
 let version:String?; let pc_core_version:String?; let execution:String?; let engine:String?; let neural_edge:String?
 let enabled:Bool?; let balance:Double?; let equity:Double?; let realized:Double?; let unrealized:Double?
 let open_positions:Int?; let max_positions:Int?; let positions:[Position]?; let trades:[Trade]?; let markets:[Market]?; let decisions:[Decision]?
 let top_markets:[String]?; let selected_market:String?; let status:String?; let scan_count:Int?; let last_error:String?; let updated_at:Double?
 let shadow:ShadowSummary?; let shadow_open:[Position]?; let shadow_trades:[Trade]?
 let market_health:MarketHealth?; let data_quality:DataQuality?; let safety:SafetyState?
}
struct ControlReply: Codable { let ok:Bool?; let action:String?; let queued:Bool? }
