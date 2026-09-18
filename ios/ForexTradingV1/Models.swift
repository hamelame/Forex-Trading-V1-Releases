import Foundation

struct Market: Codable, Identifiable { var id:String{symbol}; let symbol:String; let name:String?; let category:String?; let action:String?; let score:Double?; let regime:String?; let reason:String? }
struct Position: Codable, Identifiable { var id:String{"\(symbol)-\(side ?? "")"}; let symbol:String; let side:String?; let unrealized:Double?; let pnl:Double?; let reason:String? }
struct Decision: Codable, Identifiable { var id:String{"\(symbol)-\(at ?? ts ?? 0)"}; let symbol:String; let action:String?; let score:Double?; let regime:String?; let text:String?; let reason:String?; let at:Double?; let ts:Double? }
struct AppState: Codable { let version:String?; let pc_core_version:String?; let enabled:Bool; let balance:Double; let equity:Double; let realized:Double; let unrealized:Double; let open_positions:Int; let max_positions:Int; let positions:[Position]; let markets:[Market]; let decisions:[Decision]; let status:String?; let updated_at:Double? }
struct ControlReply: Codable { let ok:Bool? }
