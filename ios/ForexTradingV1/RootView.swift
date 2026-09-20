import SwiftUI
import Foundation

struct RootView: View {
 @EnvironmentObject var m:TradingModel
 var body: some View { Group { if m.connected { MainTabs() } else { LoginView() } }.preferredColorScheme(.dark) }
}
struct LoginView:View { @EnvironmentObject var m:TradingModel; var body:some View { VStack(spacing:18){ Spacer(); Image(systemName:"chart.xyaxis.line").font(.system(size:58)); Text("FOREX TRADING V1").font(.title.bold()); Text("PC v2.8.1 · PAPER ONLY").foregroundStyle(.yellow); SecureField("Mobile access token",text:$m.token).textFieldStyle(.roundedBorder).textInputAutocapitalization(.never); Button("Connect"){Task{await m.refresh(); if m.connected {m.connect()}}}.buttonStyle(.borderedProminent); if !m.error.isEmpty {Text(m.error).foregroundStyle(.orange)}; Spacer() }.padding() } }

struct MainTabs:View { var body:some View { TabView {
 Dashboard().tabItem{Label("Command",systemImage:"gauge.with.dots.needle.50percent")}
 Markets().tabItem{Label("Markets",systemImage:"chart.bar")}
 Decisions().tabItem{Label("AI Feed",systemImage:"brain")}
 TradesView().tabItem{Label("Trades",systemImage:"list.bullet.rectangle")}
 ShadowView().tabItem{Label("Shadow",systemImage:"waveform.path.ecg")}
 SettingsView().tabItem{Label("More",systemImage:"gear")}
} } }

struct Dashboard:View {
 @EnvironmentObject var m:TradingModel; var s:AppState?{m.state}
 var body:some View { NavigationStack{ScrollView{VStack(spacing:14){
  Text("PAPER ONLY · NEURAL EDGE SHADOW ONLY").font(.caption.bold()).foregroundStyle(.yellow)
  if m.transientError { Text(m.error).font(.caption).foregroundStyle(.orange).frame(maxWidth:.infinity,alignment:.leading) }
  HStack{Metric("EQUITY",s?.equity);Metric("SESSION P/L",(s?.realized ?? 0)+(s?.unrealized ?? 0))}
  HStack{Metric("REALIZED",s?.realized);Metric("UNREALIZED",s?.unrealized)}
  GroupBox("Market Health"){VStack(alignment:.leading,spacing:5){
   Text("\(s?.market_health?.regime ?? "COLLECTING") · score \(Int(s?.market_health?.score ?? 0))")
   Text("Risk: \(s?.market_health?.risk ?? "NORMAL") · Scan #\(s?.scan_count ?? 0)").font(.caption).foregroundStyle(.secondary)
  }.frame(maxWidth:.infinity,alignment:.leading)}
  Text(s?.status ?? "Connecting…").frame(maxWidth:.infinity,alignment:.leading)
  HStack{Button(s?.enabled == true ? "AI ACTIVE":"START AI"){Task{await m.control("start")}}; Button("PAUSE"){Task{await m.control("pause")}}}.buttonStyle(.borderedProminent)
  Text("Top markets: \((s?.top_markets ?? []).joined(separator:", "))").font(.caption).frame(maxWidth:.infinity,alignment:.leading)
  Text("Open positions \(s?.open_positions ?? 0)/\(s?.max_positions ?? 0)").font(.headline)
  ForEach(s?.positions ?? []){p in Row(p.symbol,p.side ?? "",p.unrealized ?? p.pnl ?? 0)}
  if let e=s?.last_error,!e.isEmpty {Text(e).font(.caption).foregroundStyle(.orange)}
 }.padding()}.navigationTitle("Command Center")}.task{m.connect()} }
}
struct Metric:View {let t:String;let v:Double?;init(_ t:String,_ v:Double?){self.t=t;self.v=v};var body:some View{VStack{Text(t).font(.caption);Text((v ?? 0).formatted(.currency(code:"USD"))).font(.title3.bold())}.frame(maxWidth:.infinity).padding().background(.thinMaterial).clipShape(RoundedRectangle(cornerRadius:14))}}
struct Row:View{let a:String,b:String;let n:Double?;init(_ a:String,_ b:String,_ n:Double?=nil){self.a=a;self.b=b;self.n=n};var body:some View{HStack{VStack(alignment:.leading){Text(a).bold();Text(b).font(.caption).foregroundStyle(.secondary)};Spacer();if let n{Text(n.formatted(.number.precision(.fractionLength(2))))}}.padding().background(.thinMaterial).clipShape(RoundedRectangle(cornerRadius:12))}}

struct Markets:View{
 @EnvironmentObject var m:TradingModel; @State private var search=""
 var body:some View{NavigationStack{List((m.state?.markets ?? []).filter{search.isEmpty || $0.symbol.localizedCaseInsensitiveContains(search) || ($0.name ?? "").localizedCaseInsensitiveContains(search)}){x in
  Button{Task{await m.selectMarket(x.symbol)}} label:{VStack(alignment:.leading,spacing:4){
   HStack{Text(x.symbol).bold();Spacer();Text(x.action ?? "WAIT").bold()}
   Text([x.name,x.category,x.regime].compactMap{$0}.joined(separator:" · ")).font(.caption)
   HStack{Text("Score \(Int(x.score ?? 0))");Text("Q \(Int(x.quality ?? 0))");Text(x.feed_status ?? "")}.font(.caption2).foregroundStyle(.secondary)
   if let r=x.reason,!r.isEmpty {Text(r).font(.caption2).lineLimit(2)}
  }}.buttonStyle(.plain)
 }.searchable(text:$search).navigationTitle("Markets")}}
}
struct Decisions:View{@EnvironmentObject var m:TradingModel;var body:some View{NavigationStack{List(m.state?.decisions ?? []){d in VStack(alignment:.leading){Text("\(d.symbol ?? "Market") · \(d.action ?? "WAIT")").bold();Text(d.text ?? d.reason ?? "").font(.caption)}}.overlay{if (m.state?.decisions ?? []).isEmpty{Text("AI is collecting decisions…").foregroundStyle(.secondary)}}.navigationTitle("AI Decision Feed")}}}
struct TradesView:View{@EnvironmentObject var m:TradingModel;var body:some View{NavigationStack{List(m.state?.trades ?? []){t in Row(t.symbol ?? "Trade",t.side ?? t.reason ?? "",t.pnl)}.overlay{if (m.state?.trades ?? []).isEmpty{VStack(spacing:8){Image(systemName:"tray").font(.largeTitle);Text("No closed PAPER trades").foregroundStyle(.secondary)}}}.navigationTitle("Trade Log")}}}

struct ShadowView:View{
 @EnvironmentObject var m:TradingModel
 var body:some View{NavigationStack{List{
  Section("Neural Edge"){
   LabeledContent("Runtime",value:m.state?.shadow?.active == true ? "ACTIVE":"COLLECTING")
   LabeledContent("Samples",value:"\(m.state?.shadow?.samples ?? m.state?.shadow?.observations ?? 0)")
   LabeledContent("Validation",value:m.state?.shadow?.validation_accuracy.map{String(format:"%.1f%%",$0)} ?? "--")
   LabeledContent("Win rate",value:m.state?.shadow?.win_rate.map{String(format:"%.1f%%",$0)} ?? "--")
   LabeledContent("Shadow P/L",value:(m.state?.shadow?.pnl ?? m.state?.shadow?.shadow_pnl ?? 0).formatted(.currency(code:"USD")))
  }
  if !(m.state?.shadow_open ?? []).isEmpty {Section("Shadow Open"){ForEach(m.state?.shadow_open ?? []){p in Row(p.symbol,p.side ?? "",p.unrealized ?? p.pnl)}}}
  if !(m.state?.shadow_trades ?? []).isEmpty {Section("Shadow Trades"){ForEach(m.state?.shadow_trades ?? []){t in Row(t.symbol ?? "Trade",t.side ?? "",t.pnl)}}}
  Section("Safety"){Text("SHADOW ONLY · no broker execution").foregroundStyle(.yellow)}
 }.navigationTitle("Shadow Lab")}}
}

struct SettingsView:View{
 @EnvironmentObject var m:TradingModel; @State private var confirmClose=false; @State private var capital="20000"; @State private var topN=5
 var body:some View{NavigationStack{Form{
  Section("Data Quality"){
   LabeledContent("Markets",value:"\(m.state?.data_quality?.markets ?? 0)")
   LabeledContent("Average",value:String(format:"%.1f",m.state?.data_quality?.average ?? 0))
   if let feeds=m.state?.data_quality?.feeds {ForEach(feeds.keys.sorted(),id:\.self){k in LabeledContent(k,value:"\(feeds[k] ?? 0)")}}
  }
  Section("Selection"){
   Picker("AI Watchlist",selection:$topN){Text("Top 5").tag(5);Text("Top 10").tag(10)}.onChange(of:topN){v in Task{await m.setTopN(v)}}
   if let selected=m.state?.selected_market,!selected.isEmpty {LabeledContent("Manual market",value:selected)}
  }
  Section("Paper Session"){
   TextField("Starting capital",text:$capital).keyboardType(.numberPad)
   Button("Start new PAPER session"){Task{await m.newSession(capital:Double(capital) ?? 20000)}}
  }
  Section("Safety"){Text("100% PAPER ONLY");Text("Neural Edge: SHADOW ONLY");Text("No broker-order endpoint")}
  Section("Engine"){Text("PC Core: \(m.state?.pc_core_version ?? "2.8.1")");Text("Mobile API: \(m.state?.version ?? "--")");Text("Cloud AI continues when this app is closed.")}
  Section{Button("Close all PAPER positions",role:.destructive){confirmClose=true}}
 }.confirmationDialog("Close every open PAPER position?",isPresented:$confirmClose,titleVisibility:.visible){Button("Close all",role:.destructive){Task{await m.control("close_all")}}}.navigationTitle("More")}}
}
