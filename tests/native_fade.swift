// Compiled with the production Helper by scripts/test_native.py. No radio or Keychain access.
final class FadeFixture:Helper {
    var writes=[(UInt8,[UInt8])]()
    var queries=0
    override init() {
        super.init()
        runtime=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at:runtime,withIntermediateDirectories:true)
        profile=["node":1,"id":"synthetic-light","mesh":"synthetic_mesh","mac":"010203040506"]; session=[UInt8](repeating:0,count:16); mac=[1,2,3,4,5,6]
    }
    deinit { try? FileManager.default.removeItem(at:runtime) }
    override func send(_ op:UInt8,_ params:[UInt8]) -> Bool { writes.append((op,params)); return true }
    override func queryState() { queries+=1 }
    @discardableResult func start(_ brightness:Int,_ target:Int,_ duration:Double) -> String {
        let id=UUID().uuidString
        job=["request_id":id,"command":"fade","values":["brightness":target,"duration":duration]]
        deadline=ProcessInfo.processInfo.systemUptime+min(700,max(1,duration+35))
        state=["brightness":brightness,"on":brightness>0]
        phase="fadeBaseline"; beginFade()
        return id
    }
    func stop() { fadeTimer?.invalidate(); endFadeActivity(); job=nil; activeFade=nil }
    func report(_ brightness:Int,_ white:Int=50) {
        // A synthetic encrypted status packet exercises the production parser.
        var data=[UInt8](repeating:0,count:20)
        data[7]=0xdc; data[10]=1; data[11]=1; data[12]=UInt8(brightness); data[13]=UInt8(white)
        let iv=Array(mac.prefix(3))+Array(data.prefix(5))+[UInt8](repeating:0,count:8)
        let stream=aes(session!,[0]+Array(iv.prefix(15)))
        for i in 7..<20 { data[i] ^= stream[i-7] }
        parseStatus(data)
    }
}
var failures=[String]()
func check(_ condition:Bool,_ message:String) {
    if !condition { failures.append(message); print("FAIL: \(message)") }
}
func runFor(_ seconds:Double) { RunLoop.current.run(until:Date().addingTimeInterval(seconds)) }
func waitForQuery(_ helper:FadeFixture) {
    let deadline=Date().addingTimeInterval(3)
    while helper.queries==0 && Date()<deadline { runFor(0.05) }
}
func readResponse(_ helper:Helper,_ id:String) -> [String:Any]? {
    let path=helper.runtime.appendingPathComponent("response-\(id).json")
    guard let data=try? Data(contentsOf:path),
          let obj=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any] else { return nil }
    return obj
}
func writeRequest(_ helper:Helper,_ object:[String:Any]) {
    let path=helper.runtime.appendingPathComponent("request.json")
    let data=try! JSONSerialization.data(withJSONObject:object)
    try! data.write(to:path)
}

do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(0,1,0.6); waitForQuery(helper)
    check(helper.writes.first?.0==0xd0 && helper.writes.first?.1==[1],"fade from off powers on first")
    check(!helper.writes.contains { $0.0==0xd0 && $0.1==[0] },"fade from off never powers off mid-ramp")
    check(helper.writes.last?.0==0xd2 && helper.writes.last?.1==[1],"fade reaches requested low brightness")
    check(helper.queries==1,"completed fade requests a fresh status")
    check(helper.job != nil,"sending the target alone does not report success")
    helper.report(0)
    check(helper.job != nil,"mismatching status cannot finish an on fade")
    helper.report(1)
    check(helper.job==nil && helper.fadeActivity==nil,"matching fresh status finishes and releases activity")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(100,0,0.6); waitForQuery(helper)
    let off=helper.writes.indices.filter { helper.writes[$0].0==0xd0 && helper.writes[$0].1==[0] }
    check(off==[helper.writes.count-1],"fade to zero powers off only at completion")
    check(helper.expected["on"] as? Bool==false,"fade to zero verifies power off")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(10,90,0.6)
    helper.report(90)
    check(helper.job != nil,"a matching status during the ramp cannot finish early")
    // Simulate a busy main loop: the next callback must catch up to elapsed time.
    Thread.sleep(forTimeInterval:0.7); helper.fadeTimer?.fire()
    check(helper.writes.last?.1==[90],"delayed timer catches up instead of extending the ramp")
    waitForQuery(helper)
    check(helper.queries==1,"delayed ramp still reaches verification")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(10,90,0.6)
    helper.fail("DISCONNECTED","Synthetic disconnect")
    let count=helper.writes.count; runFor(0.8)
    check(helper.writes.count==count,"failure stops subsequent fade writes")
    check(helper.fadeActivity==nil && helper.job==nil,"failure releases activity and clears the job")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    for duration in [60.0,120.0,299.0,300.0] {
        helper.start(10,90,duration)
        check(abs((helper.fadeTimer?.timeInterval ?? 0)-0.15)<0.001,"normal cadence through five minutes: \(duration)s")
        helper.stop()
    }
    helper.start(10,90,600)
    check(abs((helper.fadeTimer?.timeInterval ?? 0)-0.3)<0.001,"ten-minute fade spaces out 2000 steps")
    helper.disconnect()
    check(helper.fadeTimer==nil && helper.fadeActivity==nil,"disconnect releases timer and activity")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(20,21,60)
    helper.fadeTimer?.fire(); helper.fadeTimer?.fire()
    check(helper.writes.count==2 && helper.writes.allSatisfy { $0.1==[20] },"normal cadence retains repeated whole-percent steps")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.rememberBrightness(42)
    helper.start(42,0,0.2)
    helper.report(10) // A fade-down notification must not replace the restore target.
    waitForQuery(helper); helper.report(0)
    check(helper.previousBrightness()==42,"fade down and off preserve the last positive level")
    let permissions=try! FileManager.default.attributesOfItem(atPath:helper.runtime.appendingPathComponent("brightness-levels.json").path)
    check((permissions[.posixPermissions] as? NSNumber)?.intValue==0o600,"saved brightness file is private")
    let restarted=FadeFixture(); defer { restarted.stop() }
    try! FileManager.default.copyItem(at:helper.runtime.appendingPathComponent("brightness-levels.json"),to:restarted.runtime.appendingPathComponent("brightness-levels.json"))
    restarted.job=["request_id":UUID().uuidString,"command":"fade","values":["restore_brightness":true,"duration":0.2]]
    restarted.state=["brightness":0,"on":false]; restarted.phase="fadeBaseline"; restarted.beginFade()
    waitForQuery(restarted)
    check(restarted.writes.last?.1==[42],"fade on restores the saved level after a helper restart")
    restarted.report(42)
    check(restarted.job==nil,"restored fade on still requires matching fresh status")
    restarted.profile["id"]="different-light"
    check(restarted.previousBrightness()==nil,"brightness memory is isolated by device profile")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true
    helper.job=["request_id":UUID().uuidString,"command":"set","values":["on":false]]
    helper.execute()
    check(helper.phase=="offBaseline" && helper.writes.isEmpty,"ordinary off reads brightness before sending off")
    helper.report(37); runFor(0.05)
    check(helper.previousBrightness()==37 && helper.writes.last?.1==[0],"ordinary off remembers the actual pre-off level")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.job=["request_id":UUID().uuidString,"command":"fade","values":["restore_brightness":true,"duration":0.2]]
    helper.state=["brightness":0,"on":false]; helper.phase="fadeBaseline"; helper.beginFade()
    waitForQuery(helper)
    check(helper.writes.last?.1==[100],"fade on uses 100 only when no previous level is known")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    try! FileManager.default.createDirectory(at:helper.runtime.appendingPathComponent("brightness-levels.json"),withIntermediateDirectories:true)
    helper.ready=true
    helper.job=["request_id":UUID().uuidString,"command":"set","values":["on":false]]
    helper.execute(); helper.report(37); runFor(0.05)
    check(helper.job==nil && helper.writes.isEmpty,"failed brightness save aborts off before sending writes")
}
final class DeadlineFixture:Helper { override func handleJob() {} }
do {
    let helper=DeadlineFixture()
    helper.runtime=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try! FileManager.default.createDirectory(at:helper.runtime,withIntermediateDirectories:true)
    defer { try? FileManager.default.removeItem(at:helper.runtime) }
    let request:[String:Any]=["request_id":UUID().uuidString,"command":"fade","timeout":635.0]
    try! JSONSerialization.data(withJSONObject:request).write(to:helper.runtime.appendingPathComponent("request.json"))
    helper.poll()
    let remaining=helper.deadline-ProcessInfo.processInfo.systemUptime
    check(remaining>634 && remaining<=635,"native deadline preserves all ten-minute fade headroom")
}

// Synthetic production regressions
// 1. Ack/completion split
do {
    let helper=FadeFixture(); defer { helper.stop() }
    let reqId=UUID().uuidString
    helper.job=["request_id":reqId,"command":"fade","values":["brightness":80,"duration":0.2,"background":true]]
    helper.deadline=ProcessInfo.processInfo.systemUptime+37
    helper.state=["brightness":20,"on":true]
    helper.phase="fadeBaseline"; helper.beginFade()

    let ack=readResponse(helper,reqId)
    check(ack?["accepted"] as? Bool==true,"background fade sends initial accepted ACK")
    check(ack?["verified"] as? Bool==false,"background fade initial ACK is unverified")
    check(ack?["operation_id"] as? String==reqId,"initial ACK returns operation_id matching request UUID")
    check(helper.activeFade != nil,"background fade continues running in background")

    let fadeStatus=helper.readFadeStatus()
    check(fadeStatus?["status"] as? String=="running","fade status records running")
    let permissions=try! FileManager.default.attributesOfItem(atPath:helper.runtime.appendingPathComponent("fade-status.json").path)
    check((permissions[.posixPermissions] as? NSNumber)?.intValue==0o600,"fade-status.json permissions are private 0600")

    try! FileManager.default.removeItem(at:helper.runtime.appendingPathComponent("response-\(reqId).json"))

    waitForQuery(helper)
    helper.report(80)
    check(helper.activeFade==nil && helper.fadeActivity==nil,"background fade finishes and releases activity")
    let completedStatus=helper.readFadeStatus()
    check(completedStatus?["status"] as? String=="completed","fade status records completed")
    check((completedStatus?["state"] as? [String:Any])?["brightness"] as? Int==80,"fade status includes verified state")
    check(!FileManager.default.fileExists(atPath:helper.runtime.appendingPathComponent("response-\(reqId).json").path),"background fade does not write orphan completion response")
}

// 2. Concurrent status
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true
    helper.start(10,70,2.0)
    check(helper.activeFade != nil,"fade is active")
    let queriesBefore=helper.queries

    let statusId=UUID().uuidString
    writeRequest(helper,["request_id":statusId,"command":"status","device":helper.profile,"timeout":5.0])
    helper.poll()

    check(helper.queries>queriesBefore,"concurrent status triggers fresh query")
    check(helper.activeFade != nil,"concurrent status does not cancel active fade ramp")

    helper.report(35)
    let statusResp=readResponse(helper,statusId)
    check(statusResp?["ok"] as? Bool==true && statusResp?["verified"] as? Bool==true,"concurrent status returns verified response")
    check((statusResp?["state"] as? [String:Any])?["brightness"] as? Int==35,"status returns fresh readback")
    check(helper.activeFade != nil,"fade ramp continues running after status readback")
    check(helper.previousBrightness() != 35,"intermediate status report does not corrupt restore memory")
}

// 3. Cancel cleanup
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true
    helper.start(10,80,5.0)
    check(helper.fadeTimer != nil && helper.fadeActivity != nil,"fade timer and activity active")

    let cancelId=UUID().uuidString
    writeRequest(helper,["request_id":cancelId,"command":"fade-cancel","timeout":5.0])
    helper.poll()

    check(helper.fadeTimer==nil,"fade-cancel invalidates fade timer")
    check(helper.fadeActivity==nil,"fade-cancel releases activity")
    check(helper.activeFade==nil,"active fade cleared")
    let cancelStatus=helper.readFadeStatus()
    check(cancelStatus?["status"] as? String=="cancelled","fade-status.json marked cancelled")
    let cancelResp=readResponse(helper,cancelId)
    check((cancelResp?["operation"] as? [String:Any])?["status"] as? String=="cancelled","cancel command returns operation dictionary with cancelled status")
}

// 4. Supersession no stale writes
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true
    helper.start(10,90,2.0)
    let oldFadeGen=helper.fadeGeneration

    let setId=UUID().uuidString
    writeRequest(helper,["request_id":setId,"command":"set","device":helper.profile,"values":["brightness":50],"timeout":5.0])
    helper.poll()

    check(helper.readFadeStatus()?["status"] as? String=="cancelled","old fade marked cancelled upon supersession")
    check(helper.fadeGeneration>oldFadeGen,"fade generation incremented to invalidate stale callbacks")
    check(helper.ready==true,"connection preserved when superseding on same device")

    // Stale timer fire cannot write
    helper.fadeTimer?.fire()
    check(!helper.writes.contains { $0.0==0xd2 && $0.1==[90] },"stale ramp writes prevented")
}

// 5. Foreground cancellation
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true
    let fadeId=helper.start(10,60,2.0)
    check(helper.activeFade?.isBackground==false,"fade is foreground")

    let cancelId=UUID().uuidString
    writeRequest(helper,["request_id":cancelId,"command":"fade-cancel","timeout":5.0])
    helper.poll()

    let fadeResp=readResponse(helper,fadeId)
    check(fadeResp?["ok"] as? Bool==false,"foreground caller receives error on cancellation")
    check((fadeResp?["error"] as? [String:Any])?["code"] as? String=="CANCELLED","foreground caller receives CANCELLED code")
}

// 6. Off white error
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true
    let whiteId=UUID().uuidString
    helper.job=["request_id":whiteId,"command":"set","device":helper.profile,"values":["white":40],"timeout":35.0]
    helper.deadline=ProcessInfo.processInfo.systemUptime+35
    helper.execute()

    check(helper.phase=="colorWhiteBaseline","explicit white queries fresh baseline")
    check(helper.writes.isEmpty,"no control packet sent before power verification")

    helper.report(0) // Bulb is reported off
    let whiteResp=readResponse(helper,whiteId)
    check(whiteResp?["ok"] as? Bool==false,"prompt failure when light is off")
    check((whiteResp?["error"] as? [String:Any])?["code"] as? String=="LIGHT_OFF","returns LIGHT_OFF error code")
    check(helper.writes.isEmpty,"power preserved; light not turned on")
    check(helper.ready,"off-light validation preserves the healthy connection")
}

// 7. fade-status without device
do {
    let helper=FadeFixture(); defer { helper.stop() }
    let statusId=UUID().uuidString
    writeRequest(helper,["request_id":statusId,"command":"fade-status","timeout":5.0])
    helper.poll()
    let resp=readResponse(helper,statusId)
    check(resp?["ok"] as? Bool==true,"fade-status succeeds without device")
    check(resp?.keys.contains("operation")==true,"fade-status returns operation key")
}

// A tone control is independent of brightness, including a foreground fade.
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true; helper.rememberBrightness(20)
    helper.start(20,80,0.6)
    let fadeID=helper.jobID,toneID=UUID().uuidString
    helper.handleIncomingRequest(["command":"set","device":helper.profile,"values":["white":35]],id:toneID)
    check(helper.activeFade != nil && helper.jobID==fadeID,"tone command preserves foreground fade context")
    check(helper.writes.contains {$0.0==0xe2 && $0.1==[5,35]},"tone is applied during fade")
    helper.report(80,35)
    check(readResponse(helper,toneID)==nil,"tone readback waits until after its write settling window")
    runFor(0.55); helper.report(50,35)
    check(readResponse(helper,toneID)?["verified"] as? Bool==true,"tone independently verifies while brightness changes")
    check(helper.activeFade != nil && helper.previousBrightness()==20,"tone readback does not finish ramp or save intermediate brightness")
    runFor(0.6); helper.report(80,35)
    check(helper.activeFade==nil,"ramp independently verifies brightness after tone control")
    check(helper.readFadeStatus()?["verified"] as? Bool==true,"completed operation explicitly records verified endpoint")
}
// Every fade gets a new endpoint readback window.
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(20,80,0.2); waitForQuery(helper); helper.report(80)
    helper.start(80,50,0.2)
    runFor(0.25); helper.report(50)
    check(helper.activeFade != nil,"second fade cannot reuse first fade verification window")
    runFor(0.45); helper.report(50)
    check(helper.activeFade==nil,"second fade finishes after its own final query")
}
// Persistence errors cannot become completed operations or accepted starts.
do {
    let helper=FadeFixture(); defer { helper.stop() }
    try! FileManager.default.createDirectory(at:helper.fadeStatusPath(),withIntermediateDirectories:true)
    helper.start(20,80,0.2)
    check(helper.activeFade==nil && helper.writes.isEmpty,"unsaved start fails before ramp writes")
    check(helper.readFadeStatus()?["status"] as? String=="failed","failed persistence is visible in memory")
}
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.start(20,80,0.2); waitForQuery(helper)
    try! FileManager.default.createDirectory(at:helper.runtime.appendingPathComponent("brightness-levels.json"),withIntermediateDirectories:true)
    helper.report(80)
    check(helper.readFadeStatus()?["status"] as? String=="failed","brightness save failure cannot be overwritten by completed")
}
// A restart never advertises an interrupted ramp as still running.
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.saveFadeStatus(["operation_id":UUID().uuidString,"status":"running"])
    helper.latestFadeStatus=nil; helper.recoverInterruptedFade()
    check(helper.readFadeStatus()?["status"] as? String=="failed","restart marks interrupted running operation failed")
}
// Pending fade cancellation and ordinary request serialization.
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.job=["request_id":UUID().uuidString,"command":"fade"]
    let fadeID=helper.jobID
    helper.handleIncomingRequest(["command":"fade-cancel"],id:UUID().uuidString)
    check(helper.job==nil && (readResponse(helper,fadeID)?["error"] as? [String:Any])?["code"] as? String=="CANCELLED","cancel stops a fade before baseline/start")
    helper.job=["request_id":UUID().uuidString,"command":"status"]
    let original=helper.jobID
    helper.deadline=ProcessInfo.processInfo.systemUptime+35
    writeRequest(helper,["request_id":UUID().uuidString,"command":"set"])
    helper.poll()
    check(helper.jobID==original && FileManager.default.fileExists(atPath:helper.runtime.appendingPathComponent("request.json").path),"ordinary jobs stay queued without cancelling each other")
    var changed=helper.profile; changed["mac"]="AABBCCDDEEFF"
    check(!helper.isSameDevice(changed),"same profile ID cannot alias a different physical device")
}

// Urgent requests bypass a queued ordinary mailbox and startup has its own budget.
do {
    let helper=FadeFixture(); defer { helper.stop() }
    helper.ready=true; helper.start(20,80,5)
    let ordinaryID=UUID().uuidString,cancelID=UUID().uuidString
    writeRequest(helper,["request_id":ordinaryID,"command":"status","device":helper.profile])
    try! JSONSerialization.data(withJSONObject:["request_id":cancelID,"command":"fade-cancel"])
        .write(to:helper.runtime.appendingPathComponent("priority-\(cancelID).json"))
    helper.poll()
    check(helper.activeFade==nil && readResponse(helper,cancelID)?["ok"] as? Bool==true,"urgent cancel bypasses queued ordinary command")
    check(FileManager.default.fileExists(atPath:helper.runtime.appendingPathComponent("request.json").path),"priority handling preserves queued ordinary request")
}
do {
    let helper=DeadlineFixture()
    helper.runtime=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try! FileManager.default.createDirectory(at:helper.runtime,withIntermediateDirectories:true)
    defer { try? FileManager.default.removeItem(at:helper.runtime) }
    helper.handleIncomingRequest(["command":"fade","timeout":635.0,"values":["background":true]],id:UUID().uuidString)
    let remaining=helper.deadline-ProcessInfo.processInfo.systemUptime
    check(remaining>34 && remaining<=35,"ten-minute background fade startup is bounded to 35 seconds")
}

if failures.isEmpty { print("PASS: native fade timing, power, verification, and cleanup") }
exit(failures.isEmpty ? 0:1)
