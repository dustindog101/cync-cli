// Copyright 2018 Google LLC
// Copyright 2016 Matthew Garrett
// Copyright 2026 cync-cli contributors
// Telink protocol adapted from google/python-dimond and google/python-laurel.
// Apache-2.0; see NOTICE. Runtime device identities and secrets are never compiled in.
import Cocoa
import CoreBluetooth
import CommonCrypto
import Security

func padded(_ s: String) -> [UInt8] { Array((Array(s.utf8)+[UInt8](repeating:0,count:16)).prefix(16)) }
func aes(_ key: [UInt8], _ input: [UInt8]) -> [UInt8] {
    precondition(key.count==16 && input.count==16)
    let k=Array(key.reversed()), d=Array(input.reversed())
    var output=[UInt8](repeating:0,count:16), moved=0
    let code=k.withUnsafeBytes { kb in d.withUnsafeBytes { db in output.withUnsafeMutableBytes { ob in
        CCCrypt(CCOperation(kCCEncrypt),CCAlgorithm(kCCAlgorithmAES),CCOptions(kCCOptionECBMode),kb.baseAddress,16,nil,db.baseAddress,16,ob.baseAddress,16,&moved)
    } } }
    precondition(code==kCCSuccess && moved==16)
    return Array(output.reversed())
}
func proof(_ name: String,_ key: String,_ random: [UInt8]) -> [UInt8] {
    aes(random+[UInt8](repeating:0,count:16-random.count),zip(padded(name),padded(key)).map { $0 ^ $1 })
}
func hex(_ bytes: [UInt8]) -> String { bytes.map { String(format:"%02x",$0) }.joined() }
func unhex(_ text: String) -> [UInt8] {
    let chars=Array(text.replacingOccurrences(of:":",with:""))
    guard chars.count==12, chars.allSatisfy({$0.isHexDigit}) else { return [] }
    return stride(from:0,to:chars.count,by:2).compactMap { UInt8(String(chars[$0...$0+1]),radix:16) }
}
func encryptedPacket(_ sk: [UInt8],_ mac: [UInt8],_ seq: Int,_ node: Int,_ op: UInt8,_ params: [UInt8]) -> [UInt8] {
    precondition(sk.count==16 && mac.count>=4 && (1...255).contains(node) && params.count<=10)
    var p=[UInt8](repeating:0,count:20)
    p[0]=UInt8(seq&255); p[1]=UInt8((seq>>8)&255); p[2]=UInt8((seq>>16)&255)
    p[5]=UInt8(node); p[7]=op; p[8]=0x11; p[9]=0x02
    for (i,b) in params.enumerated() { p[10+i]=b }
    let m=Array(mac.prefix(4))
    var auth=aes(sk,m+[1]+Array(p.prefix(3))+[15]+[UInt8](repeating:0,count:7))
    for i in 0..<15 { auth[i] ^= p[i+5] }
    let mic=aes(sk,auth); p[3]=mic[0]; p[4]=mic[1]
    let stream=aes(sk,[0]+m+[1]+Array(p.prefix(3))+[UInt8](repeating:0,count:7))
    for i in 0..<15 { p[i+5] ^= stream[i] }
    return p
}
func jsonOut(_ object: [String:Any]) {
    if let data=try? JSONSerialization.data(withJSONObject:object,options:[.sortedKeys]),let line=String(data:data,encoding:.utf8) { print(line) }
}
func stdinObject() -> [String:Any] { (try? JSONSerialization.jsonObject(with:FileHandle.standardInput.readDataToEndOfFile())) as? [String:Any] ?? [:] }
let keyService="io.cync-cli.mesh"
func keyQuery(_ name: String) -> [String:Any] { [kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:keyService,kSecAttrAccount as String:name] }
func readKey(_ name: String) -> [String:Any]? {
    var q=keyQuery(name); q[kSecReturnData as String]=true; q[kSecMatchLimit as String]=kSecMatchLimitOne
    var result:CFTypeRef?
    guard SecItemCopyMatching(q as CFDictionary,&result)==errSecSuccess,let data=result as? Data else { return nil }
    return (try? JSONSerialization.jsonObject(with:data)) as? [String:Any]
}
func storeKey(_ name: String,_ key: String) -> OSStatus {
    guard !name.isEmpty,name.utf8.count<=16,!key.isEmpty,key.utf8.count<=16 else { return errSecParam }
    let data=try! JSONSerialization.data(withJSONObject:["name":name,"key":key])
    let q=keyQuery(name),attrs=[kSecValueData as String:data]
    let code=SecItemUpdate(q as CFDictionary,attrs as CFDictionary)
    if code != errSecItemNotFound { return code }
    var add=q; add[kSecValueData as String]=data
    add[kSecAttrAccessible as String]=kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    return SecItemAdd(add as CFDictionary,nil)
}
if let mode=CommandLine.arguments.dropFirst().first,mode.hasPrefix("--keychain-") {
    let input=stdinObject(),name=input["name"] as? String ?? ""
    switch mode {
    case "--keychain-store":
        let code=storeKey(name,input["key"] as? String ?? "")
        jsonOut(code==errSecSuccess ? ["ok":true] : ["ok":false,"error":["code":"KEYCHAIN_WRITE_FAILED","message":"Could not securely save the bulb key. Unlock Keychain and allow this helper access."]])
    case "--keychain-status": jsonOut(["ok":true,"available":readKey(name) != nil])
    case "--keychain-forget":
        let code=SecItemDelete(keyQuery(name) as CFDictionary)
        jsonOut(["ok":code==errSecSuccess || code==errSecItemNotFound])
    case "--keychain-import-legacy":
        let q:[String:Any]=[kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:"local.codex.ge-bulb-control",kSecReturnAttributes as String:true,kSecMatchLimit as String:kSecMatchLimitAll]
        var result:CFTypeRef?
        let code=SecItemCopyMatching(q as CFDictionary,&result)
        var devices=[[String:Any]]()
        if code==errSecSuccess {
            let records=(result as? [[String:Any]]) ?? ((result as? [String:Any]).map { [$0] } ?? [])
            for record in records {
                guard let account=record[kSecAttrAccount as String] as? String else { continue }
                let item:[String:Any]=[kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:"local.codex.ge-bulb-control",kSecAttrAccount as String:account,kSecReturnData as String:true,kSecMatchLimit as String:kSecMatchLimitOne]
                var dataResult:CFTypeRef?
                guard SecItemCopyMatching(item as CFDictionary,&dataResult)==errSecSuccess,let data=dataResult as? Data else { continue }
                guard let v=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],let mesh=v["name"] as? String,let key=v["key"] as? String,let mac=v["bulbMAC"] as? String,unhex(mac).count==6,let node=v["target"] as? Int,(1...255).contains(node) else { continue }
                guard storeKey(mesh,key)==errSecSuccess else { continue }
                devices.append(["name":v["displayName"] as? String ?? "C by GE bulb","mesh":mesh,"mac":hex(unhex(mac)),"node":node,"peripheral_id":NSNull()])
            }
        }
        jsonOut(["ok":code==errSecSuccess || code==errSecItemNotFound,"devices":devices,"status":code])
    default:jsonOut(["ok":false,"error":["code":"INVALID_COMMAND","message":"Unknown Keychain operation."]])
    }
    exit(0)
}
if CommandLine.arguments.contains("--crypto-self-test") {
    let random:[UInt8]=[0xa0,0xa1,0xa2,0xa3,0xa4,0xa5,0xa6,0xa7]
    let auth=hex([0x0c]+random+Array(proof("telink_mesh1","123",random).prefix(8)))
    let sk=aes(zip(padded("test_mesh"),padded("test_key")).map { $0 ^ $1 },Array(0...15))
    let packet=encryptedPacket(sk,[1,2,3,4,5,6],0x1234,7,0xd0,[1])
    let ok=auth=="0ca0a1a2a3a4a5a6a78db674711b855a79" && hex(sk)=="4dd0bcdea1361f61f048f4872578e15b" && hex(packet)=="3412009f2141c8c3c90dedeac8180e57d8657912"
    jsonOut(["ok":ok]); exit(ok ? 0:1)
}

struct ActiveFade {
    let operationId: String
    let isBackground: Bool
    let target: Int
    let duration: Double
    let startedUptime: Double
    let deadline: Double
    let profileId: String?
}

struct ObserverRequest {
    let requestId: String
    let command: String
    let deadline: Double
    var expected: [String:Any]? = nil
    var verifyAfter: Double = 0
}

class Helper:NSObject,NSApplicationDelegate,CBCentralManagerDelegate,CBPeripheralDelegate {
    let prefix="00010203-0405-0607-0809-0A0B0C0D"
    var runtime:URL!
    var central:CBCentralManager!
    var peer:CBPeripheral?
    var disconnecting=Set<UUID>(),queuedPeer:CBPeripheral?
    var pair:CBCharacteristic?,control:CBCharacteristic?,notify:CBCharacteristic?
    var profile=[String:Any](),metadata=[String:Any](),state=[String:Any]()
    var session:[UInt8]?,mac=[UInt8](),appRandom=[UInt8](),credential:[String:Any]?
    var job:[String:Any]?,expected=[String:Any](),ready=false,handshaking=false
    var servicesPending=0,seq=Int.random(in:1...0xfffffe),phase="",lastActivity=Date()
    var discovery=[String:[String:Any]](),infoValues=[String:String]()
    var deadline=0.0,verifyAfter=Double.greatestFiniteMagnitude
    var fadeTimer:Timer?,pollTimer:Timer?,fadeActivity:NSObjectProtocol?
    var generation=0
    var activeFade:ActiveFade?
    var fadeGeneration=0
    var fadeVerifying=false
    var fadeVerifyAfter=Double.greatestFiniteMagnitude
    var observers=[ObserverRequest]()
    var latestFadeStatus:[String:Any]?

    var jobID:String { job?["request_id"] as? String ?? "" }
    var node:Int { profile["node"] as? Int ?? 0 }

    func applicationDidFinishLaunching(_ notification:Notification) {
        let args=CommandLine.arguments
        guard let index=args.firstIndex(of:"--runtime"),args.indices.contains(index+1) else { NSApp.terminate(nil); return }
        runtime=URL(fileURLWithPath:args[index+1],isDirectory:true)
        try? FileManager.default.createDirectory(at:runtime,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        recoverInterruptedFade()
        central=CBCentralManager(delegate:self,queue:.main,options:[CBCentralManagerOptionShowPowerAlertKey:true])
        pollTimer=Timer.scheduledTimer(withTimeInterval:0.1,repeats:true) { [weak self] _ in self?.poll() }
    }

    func fadeStatusPath() -> URL { runtime.appendingPathComponent("fade-status.json") }
    func readFadeStatus() -> [String:Any]? {
        if let cached=latestFadeStatus { return cached }
        guard let data=try? Data(contentsOf:fadeStatusPath()),
              let obj=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any] else { return nil }
        return obj
    }
    @discardableResult func saveFadeStatus(_ object:[String:Any]) -> Bool {
        var record=object
        record["verified"]=(object["status"] as? String == "completed")
        latestFadeStatus=record
        guard let data=try? JSONSerialization.data(withJSONObject:record,options:[.sortedKeys]) else { return false }
        do {
            try data.write(to:fadeStatusPath(),options:.atomic)
            try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:fadeStatusPath().path)
            return true
        } catch { return false }
    }
    func recoverInterruptedFade() {
        if let old=readFadeStatus(),old["status"] as? String == "running" {
            saveFadeStatus(["operation_id":old["operation_id"] ?? "", "status":"failed",
                            "error":["code":"HELPER_RESTARTED","message":"The helper stopped before verifying this fade."]])
        }
    }

    func cancelColorObservers() {
        observers.removeAll { obs in
            guard obs.expected != nil else { return false }
            self.writeResponse(["schema_version":1,"request_id":obs.requestId,"ok":false,"verified":false,
                                "error":["code":"CANCELLED","message":"Color was replaced by a new control."]])
            return true
        }
    }

    func cancelActiveFade(reason:String) {
        guard activeFade != nil else { return }
        fadeGeneration+=1
        fadeTimer?.invalidate(); fadeTimer=nil
        fadeVerifying=false
        endFadeActivity()
        activeFade=nil
    }

    func isSameDevice(_ requested:[String:Any]?) -> Bool {
        guard let req=requested else { return false }
        guard let mesh=req["mesh"] as? String, let macStr=req["mac"] as? String, let target=req["node"] as? Int else { return false }
        return mesh==(profile["mesh"] as? String) && macStr==(profile["mac"] as? String) && target==node
    }

    func poll() {
        let uptime=ProcessInfo.processInfo.systemUptime
        if let fade=activeFade, uptime>fade.deadline {
            let id=fade.operationId
            cancelActiveFade(reason:"timed out")
            saveFadeStatus(["operation_id":id,"status":"failed","error":["code":"TIMEOUT","message":"Bulb did not confirm the fade ramp. Check range and Bluetooth permission."]])
            if !fade.isBackground {
                writeResponse(["schema_version":1,"request_id":id,"ok":false,"verified":false,"connected":false,"error":["code":"TIMEOUT","message":"Bulb did not confirm the fade ramp. Check range and Bluetooth permission."]])
                if jobID==id { job=nil; expected=[:] }
            }
            disconnect()
        }
        observers.removeAll { obs in
            if uptime>obs.deadline {
                self.writeResponse(["schema_version":1,"request_id":obs.requestId,"ok":false,"verified":false,"connected":self.ready,"error":["code":"TIMEOUT","message":"Status request timed out."]])
                return true
            }
            return false
        }
        if job != nil && activeFade==nil {
            if uptime>deadline { fail("TIMEOUT","Bulb did not confirm the request. Check range, Bluetooth permission and other connected apps.") }
        }
        if ready && job==nil && activeFade==nil && observers.isEmpty && Date().timeIntervalSince(lastActivity)>90 { disconnect() }
        let urgent=(try? FileManager.default.contentsOfDirectory(at:runtime,includingPropertiesForKeys:nil))?
            .filter { $0.lastPathComponent.hasPrefix("priority-") && $0.pathExtension=="json" }
            .sorted { $0.lastPathComponent<$1.lastPathComponent }.first
        let path=urgent ?? runtime.appendingPathComponent("request.json")
        guard let data=try? Data(contentsOf:path) else { return }
        guard let object=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],let id=object["request_id"] as? String,UUID(uuidString:id) != nil else {
            try? FileManager.default.removeItem(at:path); return
        }
        let incoming=object["command"] as? String ?? ""
        if job != nil && activeFade==nil && command() != "fade" && !["fade-status","fade-cancel","doctor"].contains(incoming) { return }
        try? FileManager.default.removeItem(at:path)
        handleIncomingRequest(object,id:id)
    }

    func handleIncomingRequest(_ object:[String:Any],id:String) {
        lastActivity=Date()
        let cmd=object["command"] as? String ?? ""

        if cmd=="fade-status" {
            var op:Any=NSNull()
            if let fade=activeFade {
                op=["operation_id":fade.operationId,"status":"running"]
            } else if command()=="fade",job != nil {
                op=["operation_id":jobID,"status":"starting","verified":false]
            } else if let saved=readFadeStatus() {
                op=saved
            }
            writeResponse(["schema_version":1,"request_id":id,"ok":true,"connected":ready,"operation":op])
            return
        }

        if cmd=="fade-cancel" {
            var op:Any=NSNull()
            if let fade=activeFade {
                let fadeId=fade.operationId
                let wasForeground = !fade.isBackground
                cancelActiveFade(reason:"cancelled")
                let cancelledOp:[String:Any]=["operation_id":fadeId,"status":"cancelled"]
                saveFadeStatus(cancelledOp)
                if wasForeground {
                    writeResponse(["schema_version":1,"request_id":fadeId,"ok":false,"verified":false,"connected":ready,"error":["code":"CANCELLED","message":"Fade was cancelled."]])
                    if jobID==fadeId { job=nil; expected=[:] }
                }
                op=cancelledOp
                if !observers.isEmpty { queryState() }
            } else if command()=="fade",job != nil {
                let pendingID=jobID
                fail("CANCELLED","Fade was cancelled before starting.")
                saveFadeStatus(["operation_id":pendingID,"status":"cancelled"])
                op=readFadeStatus() ?? [:]
            } else if let saved=readFadeStatus() {
                op=saved
            }
            writeResponse(["schema_version":1,"request_id":id,"ok":true,"connected":ready,"operation":op])
            return
        }

        if cmd=="doctor" {
            writeResponse(["schema_version":1,"request_id":id,"ok":true,"connected":ready,"verified":true,"bluetooth_state":central?.state.rawValue ?? 0,"authorization":CBManager.authorization.rawValue])
            return
        }

        if (cmd=="status" || cmd=="info") && activeFade != nil && ready && isSameDevice(object["device"] as? [String:Any]) {
            let obsTimeout=(object["timeout"] as? Double) ?? (object["timeout"] as? NSNumber)?.doubleValue ?? 35
            let obsDeadline=ProcessInfo.processInfo.systemUptime+min(700,max(1,obsTimeout))
            observers.append(ObserverRequest(requestId:id,command:cmd,deadline:obsDeadline))
            queryState()
            return
        }

        if cmd=="set",activeFade != nil,ready,isSameDevice(object["device"] as? [String:Any]),
           let v=object["values"] as? [String:Any],!v.isEmpty,
           Set(v.keys).isSubset(of:["white","rgb"]) {
            var desired=[String:Any](),payload=[UInt8]()
            if v.count==1,let w=v["white"] as? Int,(0...100).contains(w) {
                desired=["white":w]; payload=[5,UInt8(w)]
            } else if v.count==1,let rgb=v["rgb"] as? [Int],rgb.count==3,rgb.allSatisfy({(0...255).contains($0)}) {
                desired=["rgb":rgb]; payload=[4]+rgb.map { UInt8($0) }
            } else {
                writeResponse(["schema_version":1,"request_id":id,"ok":false,"verified":false,
                               "error":["code":"INVALID_VALUE","message":"Choose one valid white or RGB value."]]); return
            }
            cancelColorObservers()
            let timeout=min(35,max(1,object["timeout"] as? Double ?? 35))
            let now=Date().timeIntervalSince1970
            observers.append(ObserverRequest(requestId:id,command:cmd,deadline:ProcessInfo.processInfo.systemUptime+timeout,
                                             expected:desired,verifyAfter:now+0.5))
            if !send(0xe2,payload) { fail("GATT_WRITE_FAILED","Could not send the color control."); return }
            after(0.5) {
                if self.observers.contains(where:{$0.requestId==id}) { self.queryState() }
            }
            return
        }

        cancelColorObservers()

        if let fade=activeFade {
            let fadeId=fade.operationId
            let wasForeground = !fade.isBackground
            cancelActiveFade(reason:"superseded")
            saveFadeStatus(["operation_id":fadeId,"status":"cancelled"])
            if wasForeground {
                writeResponse(["schema_version":1,"request_id":fadeId,"ok":false,"verified":false,"connected":ready,"error":["code":"CANCELLED","message":"Fade was superseded by a new command."]])
                if jobID==fadeId { job=nil; expected=[:] }
            }
        }

        if job != nil {
            fail("CANCELLED","Request was superseded by a new command.")
        }

        generation+=1; job=object; expected=[:]; verifyAfter=Double.greatestFiniteMagnitude
        let reqTimeout=(object["timeout"] as? Double) ?? (object["timeout"] as? NSNumber)?.doubleValue ?? 35
        let startupOnly=cmd=="fade" && (object["values"] as? [String:Any])?["background"] as? Bool == true
        deadline=ProcessInfo.processInfo.systemUptime+min(startupOnly ? 35:700,max(1,reqTimeout))
        handleJob()
    }

    func command() -> String { job?["command"] as? String ?? "" }
    func values() -> [String:Any] { job?["values"] as? [String:Any] ?? [:] }

    func handleJob() {
        guard job != nil else { return }
        if command()=="shutdown" { disconnect(); finish(["verified":true]); NSApp.terminate(nil); return }
        if command()=="doctor" { finish(["verified":true,"bluetooth_state":central?.state.rawValue ?? 0,"authorization":CBManager.authorization.rawValue]); return }
        if command()=="disconnect" { disconnect(); finish(["verified":true]); return }
        guard ["status","info","set","fade"].contains(command()) || command()=="scan" else { fail("INVALID_COMMAND","Unknown native operation."); return }
        if command()=="scan" {
            guard let c=central,c.state == .poweredOn else {
                if central?.state == .unknown || central?.state == .resetting { phase="waitingBluetooth"; return }
                fail(central?.state == .unauthorized ? "BLUETOOTH_PERMISSION":"BLUETOOTH_OFF","Enable Bluetooth and allow Cync Bluetooth Helper in System Settings → Privacy & Security → Bluetooth."); return
            }
            disconnect(); discovery=[:]; phase="scanning"
            c.scanForPeripherals(withServices:nil,options:[CBCentralManagerScanOptionAllowDuplicatesKey:true])
            let scanDuration=(values()["duration"] as? Double) ?? (values()["duration"] as? NSNumber)?.doubleValue ?? 5
            let seconds=min(30,max(1,scanDuration)),id=jobID
            after(seconds) { if self.jobID==id { self.central?.stopScan(); self.finish(["verified":true,"devices":self.discovery.values.sorted { ($0["rssi"] as? Int ?? -999)>($1["rssi"] as? Int ?? -999) }]) } }
            return
        }
        guard let requested=job?["device"] as? [String:Any],let mesh=requested["mesh"] as? String,!mesh.isEmpty,mesh.utf8.count<=16,let target=(requested["node"] as? Int) ?? (requested["node"] as? NSNumber)?.intValue,(1...255).contains(target),unhex(requested["mac"] as? String ?? "").count==6 else { fail("INVALID_DEVICE","Device metadata is incomplete or unsupported."); return }
        if ready,isSameDevice(requested),central == nil || central.state == .poweredOn {
            profile=requested; execute(); return
        }
        guard let c=central,c.state == .poweredOn else {
            if central?.state == .unknown || central?.state == .resetting { phase="waitingBluetooth"; return }
            fail(central?.state == .unauthorized ? "BLUETOOTH_PERMISSION":"BLUETOOTH_OFF","Enable Bluetooth and allow Cync Bluetooth Helper in System Settings → Privacy & Security → Bluetooth."); return
        }
        disconnect(); profile=requested; state=[:]; infoValues=[:]; metadata=[:]
        guard let saved=readKey(mesh),let name=saved["name"] as? String,let key=saved["key"] as? String,name==mesh,!key.isEmpty,key.utf8.count<=16 else { fail("KEY_UNAVAILABLE","Saved bulb key is unavailable. Unlock Keychain, import an existing key, or run cync auth login."); return }
        credential=saved; mac=Array(unhex(requested["mac"] as? String ?? "").reversed())
        phase="connecting"
        if let text=requested["peripheral_id"] as? String,let uuid=UUID(uuidString:text),let known=central.retrievePeripherals(withIdentifiers:[uuid]).first { connect(known) }
        else { c.scanForPeripherals(withServices:nil,options:[CBCentralManagerScanOptionAllowDuplicatesKey:true]) }
    }

    func centralManagerDidUpdateState(_ central:CBCentralManager) {
        if job != nil && phase=="waitingBluetooth" { handleJob() }
        else if (job != nil || activeFade != nil || !observers.isEmpty),
                central.state == .unauthorized || central.state == .poweredOff || central.state == .unsupported {
            fail(central.state == .unauthorized ? "BLUETOOTH_PERMISSION":"BLUETOOTH_OFF",
                 "Enable Bluetooth and allow Cync Bluetooth Helper in macOS privacy settings.")
        }
    }

    func centralManager(_ central:CBCentralManager,didDiscover p:CBPeripheral,advertisementData:[String:Any],rssi:NSNumber) {
        let name=advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? p.name ?? ""
        let bytes=Array((advertisementData[CBAdvertisementDataManufacturerDataKey] as? Data) ?? Data())
        if phase=="scanning" {
            guard bytes.starts(with:[0x11,0x02]) else { return }
            discovery[p.identifier.uuidString]=["peripheral_id":p.identifier.uuidString,"name":name,"rssi":rssi,"manufacturer":"Telink","connectable":advertisementData[CBAdvertisementDataIsConnectable] as? Bool ?? false]
        } else if phase=="connecting",name==profile["mesh"] as? String {
            let match=bytes.count>=8 && mac.count>=4 && (0...(bytes.count-4)).contains { Array(bytes[$0..<($0+4)])==Array(mac.prefix(4)) }
            if match { connect(p) }
        }
    }

    func connect(_ p:CBPeripheral) {
        central?.stopScan()
        if disconnecting.contains(p.identifier) { queuedPeer=p; return }
        peer=p; p.delegate=self; central?.connect(p)
    }

    func centralManager(_ central:CBCentralManager,didConnect p:CBPeripheral) { p.discoverServices(nil) }
    func centralManager(_ central:CBCentralManager,didFailToConnect p:CBPeripheral,error:Error?) { fail("CONNECTION_FAILED","Could not connect to the bulb. Close other apps using its Bluetooth connection.") }
    func centralManager(_ central:CBCentralManager,didDisconnectPeripheral p:CBPeripheral,error:Error?) {
        if disconnecting.remove(p.identifier) != nil {
            if let next=queuedPeer,next.identifier==p.identifier { queuedPeer=nil; connect(next) }
            return
        }
        guard p===peer else { return }
        ready=false; session=nil
        if let fade=activeFade {
            let id=fade.operationId
            cancelActiveFade(reason:"disconnected")
            saveFadeStatus(["operation_id":id,"status":"failed","error":["code":"DISCONNECTED","message":"The Bluetooth connection ended before verification."]])
            if !fade.isBackground {
                writeResponse(["schema_version":1,"request_id":id,"ok":false,"verified":false,"connected":false,"error":["code":"DISCONNECTED","message":"The Bluetooth connection ended before verification."]])
                if jobID==id { job=nil; expected=[:] }
            }
        }
        for obs in observers {
            writeResponse(["schema_version":1,"request_id":obs.requestId,"ok":false,"verified":false,"connected":false,"error":["code":"DISCONNECTED","message":"The Bluetooth connection ended before verification."]])
        }
        observers.removeAll()
        if job != nil { fail("DISCONNECTED","The Bluetooth connection ended before verification.") }
    }

    func peripheral(_ p:CBPeripheral,didDiscoverServices error:Error?) {
        guard error==nil else { fail("GATT_ERROR","Service discovery failed."); return }
        servicesPending=p.services?.count ?? 0
        metadata["services"]=(p.services ?? []).map { $0.uuid.uuidString }
        for s in p.services ?? [] { p.discoverCharacteristics(nil,for:s) }
    }

    func peripheral(_ p:CBPeripheral,didDiscoverCharacteristicsFor service:CBService,error:Error?) {
        guard error==nil else { fail("GATT_ERROR","Characteristic discovery failed."); return }
        for c in service.characteristics ?? [] {
            let uuid=c.uuid.uuidString.uppercased()
            if uuid==prefix+"1914" { pair=c }
            if uuid==prefix+"1912" { control=c }
            if uuid==prefix+"1911" { notify=c }
            if ["2A24","2A26","2A27","2A29"].contains(uuid),c.properties.contains(.read) { p.readValue(for:c) }
        }
        servicesPending-=1
        if servicesPending==0 { after(0.3) { self.authenticate() } }
    }

    func authenticate() {
        guard job != nil,let p=peer,let pair=pair,control != nil,notify != nil,let v=credential,let name=v["name"] as? String,let key=v["key"] as? String,!handshaking else {
            if job != nil { fail("UNSUPPORTED_DEVICE","Required Telink characteristics are unavailable.") }; return
        }
        handshaking=true; phase="authenticating"; appRandom=[UInt8](repeating:0,count:8)
        guard appRandom.withUnsafeMutableBytes({ SecRandomCopyBytes(kSecRandomDefault,8,$0.baseAddress!) })==errSecSuccess else { fail("CRYPTO_ERROR","Could not generate a secure session challenge."); return }
        p.writeValue(Data([0x0c]+appRandom+Array(proof(name,key,appRandom).prefix(8))),for:pair,type:.withResponse)
    }

    func peripheral(_ p:CBPeripheral,didWriteValueFor c:CBCharacteristic,error:Error?) {
        guard p===peer else { return }
        if error != nil { fail("GATT_WRITE_FAILED","The bulb rejected a Bluetooth write."); return }
        if c===pair { after(0.2) { if self.peer===p { p.readValue(for:c) } } }
    }

    func peripheral(_ p:CBPeripheral,didUpdateNotificationStateFor c:CBCharacteristic,error:Error?) {
        guard error==nil,c===notify,session != nil else { fail("NOTIFY_FAILED","Could not enable bulb status reports."); return }
        p.writeValue(Data([1]),for:c,type:.withResponse)
        ready=true; phase="ready"; credential=nil; execute()
    }

    func peripheral(_ p:CBPeripheral,didUpdateValueFor c:CBCharacteristic,error:Error?) {
        guard p===peer else { return }
        if error != nil { if c===pair { fail("AUTH_FAILED","The bulb rejected authentication. Its saved mesh key may have changed.") }; return }
        guard let value=c.value else { return }
        let data=Array(value),uuid=c.uuid.uuidString.uppercased()
        if ["2A24","2A26","2A27","2A29"].contains(uuid) {
            if uuid=="2A24" { metadata["type"]=Int(data.first ?? 0) }
            else { infoValues[uuid]=String(data:value,encoding:.utf8)?.trimmingCharacters(in:.controlCharacters) ?? "" }
        } else if c===pair {
            guard let v=credential,let name=v["name"] as? String,let key=v["key"] as? String,data.count>=17 else { fail("AUTH_FAILED","The bulb did not return a valid authentication response."); return }
            let random=Array(data[1..<9])
            guard Array(proof(name,key,random).prefix(8))==Array(data[9..<17]) else { fail("AUTH_FAILED","The saved key did not authenticate this bulb. No control commands were sent."); return }
            session=aes(zip(padded(name),padded(key)).map { $0 ^ $1 },appRandom+random)
            if let n=notify { p.setNotifyValue(true,for:n) }
        } else if c===notify { parseStatus(data) }
    }

    func parseStatus(_ original:[UInt8]) {
        guard let sk=session,mac.count>=3,original.count>=19 else { return }
        var data=original
        let iv=Array(mac.prefix(3))+Array(data.prefix(5))+[UInt8](repeating:0,count:8)
        let stream=aes(sk,[0]+Array(iv.prefix(15)))
        for i in 7..<min(data.count,23) { data[i] ^= stream[i-7] }
        guard data[7]==0xdc else { return }
        for i in [10,14] where data.count>=i+4 && Int(data[i])==node && data[i+1] != 0 {
            let brightness=Int(data[i+2]&0x7f),rgb=data[i+2]>=128,now=Date().timeIntervalSince1970
            state=["on":brightness>0,"brightness":brightness,"color_mode":rgb ? "rgb":"white","observed_at":now]
            if rgb { state["rgb"]=[Int((data[i+3]&0xe0)>>5)*255/7,Int((data[i+3]&0x1c)>>2)*255/7,Int(data[i+3]&3)*255/3] }
            else { state["white"]=Int(data[i+3]) }

            if !observers.isEmpty {
                let current=observers
                observers.removeAll()
                for obs in current {
                    if let desired=obs.expected {
                        if now<obs.verifyAfter { observers.append(obs); continue }
                        if state["on"] as? Bool != true {
                            writeResponse(["schema_version":1,"request_id":obs.requestId,"ok":false,"verified":false,
                                           "error":["code":"LIGHT_OFF","message":"The light is off; color/white readback requires power on."]])
                            continue
                        }
                        if !matches(desired) { observers.append(obs); continue }
                    }
                    var extra:[String:Any]=["verified":true,"state":state]
                    if obs.command=="info" { extra["info"]=["firmware":infoValues["2A26"] ?? "","hardware":infoValues["2A27"] ?? "","manufacturer":infoValues["2A29"] ?? "","type":metadata["type"] ?? NSNull(),"services":metadata["services"] ?? [],"peripheral_id":peer?.identifier.uuidString ?? ""] }
                    var resp:[String:Any]=["schema_version":1,"request_id":obs.requestId,"ok":true,"connected":ready]
                    for (k,v) in extra { resp[k]=v }
                    writeResponse(resp)
                }
            }

            if phase=="colorWhiteBaseline" {
                let isOn=(state["on"] as? Bool == true) && ((state["brightness"] as? Int ?? 0) > 0)
                if !isOn {
                    fail("LIGHT_OFF","The light is off. Explicit color or white commands preserve power and require the light to be on.")
                    return
                }
                phase="colorWhiteBaselineReady"
                execute()
                continue
            }

            if activeFade != nil {
                if phase=="fadeBaseline" { rememberBrightness(brightness); beginFade(); continue }
                if let fade=activeFade,fadeVerifying && now>=fadeVerifyAfter && matches(["brightness":fade.target,"on":fade.target>0]) {
                    rememberBrightness(state["brightness"] as? Int ?? fade.target)
                    guard activeFade?.operationId==fade.operationId else { return }
                    guard saveFadeStatus(["operation_id":fade.operationId,"status":"completed","state":state]) else {
                        fail("STATE_SAVE_FAILED","Could not save the final fade result."); return
                    }
                    fadeGeneration+=1
                    endFadeActivity()
                    activeFade=nil; fadeVerifying=false; phase="idle"; lastActivity=Date()
                    if !fade.isBackground {
                        finish(["verified":true,"state":state])
                    }
                    continue
                }
                continue
            }

            guard job != nil else { rememberBrightness(brightness); continue }
            if phase=="fadeBaseline" { rememberBrightness(brightness); beginFade(); continue }
            if phase=="offBaseline" { rememberBrightness(brightness); phase="offBaselineReady"; execute(); continue }
            if now>=verifyAfter, matches() {
                rememberBrightness(brightness)
                var extra:[String:Any]=["verified":true,"state":state]
                if command()=="info" { extra["info"]=["firmware":infoValues["2A26"] ?? "","hardware":infoValues["2A27"] ?? "","manufacturer":infoValues["2A29"] ?? "","type":metadata["type"] ?? NSNull(),"services":metadata["services"] ?? [],"peripheral_id":peer?.identifier.uuidString ?? ""] }
                finish(extra)
            }
        }
    }

    func matches() -> Bool { matches(expected) }
    func matches(_ expected:[String:Any]) -> Bool {
        if let on=expected["on"] as? Bool,state["on"] as? Bool != on { return false }
        if let b=expected["brightness"] as? Int,abs((state["brightness"] as? Int ?? -999)-b)>1 { return false }
        if let w=expected["white"] as? Int,state["color_mode"] as? String != "white" || abs((state["white"] as? Int ?? -999)-w)>1 { return false }
        if let desired=expected["rgb"] as? [Int] {
            guard state["color_mode"] as? String=="rgb",let got=state["rgb"] as? [Int],got.count==3 else { return false }
            for i in 0..<3 where abs(got[i]-desired[i])>[37,37,85][i] { return false }
        }
        return true
    }

    @discardableResult func send(_ op:UInt8,_ params:[UInt8]) -> Bool {
        guard ready,let p=peer,let c=control,let sk=session,mac.count>=4 else { return false }
        let packet=encryptedPacket(sk,mac,seq,node,op,params)
        seq=(seq+1)&0xffffff; if seq==0 { seq=1 }
        p.writeValue(Data(packet),for:c,type:.withResponse); return true
    }

    func queryState() {
        _=send(0xda,[])
        if let p=peer,let n=notify { after(0.25) { if self.ready,self.peer===p { p.writeValue(Data([1]),for:n,type:.withResponse) } } }
    }

    func execute() {
        guard job != nil,ready else { return }
        let v=values()
        if command()=="fade" {
            let restore=v["restore_brightness"] as? Bool == true
            let b=(v["brightness"] as? Int) ?? (v["brightness"] as? NSNumber)?.intValue
            let validTarget=restore ? v["brightness"]==nil : b.map { (0...100).contains($0) } ?? false
            let dur=(v["duration"] as? Double) ?? (v["duration"] as? NSNumber)?.doubleValue
            guard validTarget,let duration=dur,(0.2...600).contains(duration) else { fail("INVALID_VALUE","Fade requires brightness 0–100 or restore_brightness, and duration 0.2–600 seconds (10 minutes)."); return }
            phase="fadeBaseline"; queryState(); return
        }
        let setBrightness=(v["brightness"] as? Int) ?? (v["brightness"] as? NSNumber)?.intValue
        if command()=="set",v["on"] as? Bool == false || setBrightness == 0,phase != "offBaselineReady" {
            phase="offBaseline"; queryState(); return
        }
        var packets=[(UInt8,[UInt8])](); expected=[:]
        if command()=="set" {
            guard v["rgb"]==nil || v["white"]==nil else { fail("INVALID_VALUE","Choose RGB or white, not both."); return }
            let explicitColorOrWhite=(v["rgb"] != nil || v["white"] != nil) && v["on"] == nil && v["brightness"] == nil
            if explicitColorOrWhite && phase != "colorWhiteBaselineReady" {
                phase="colorWhiteBaseline"; queryState(); return
            }
            if let rgb=v["rgb"] {
                guard let rgbList=rgb as? [Any] else { fail("INVALID_VALUE","RGB values must be 0–255."); return }
                let parsedRgb=rgbList.compactMap { ($0 as? Int) ?? ($0 as? NSNumber)?.intValue }
                guard parsedRgb.count==3,parsedRgb.allSatisfy({(0...255).contains($0)}) else { fail("INVALID_VALUE","RGB values must be 0–255."); return }
                expected["rgb"]=parsedRgb; packets.append((0xe2,[4]+parsedRgb.map { UInt8($0) }))
            }
            if let w=(v["white"] as? Int) ?? (v["white"] as? NSNumber)?.intValue {
                guard (0...100).contains(w) else { fail("INVALID_VALUE","White warmth must be 0–100."); return }
                expected["white"]=w; packets.append((0xe2,[5,UInt8(w)]))
            }
            if let b=setBrightness {
                guard (0...100).contains(b) else { fail("INVALID_VALUE","Brightness must be 0–100."); return }
                expected["brightness"]=b
                if b>0 {
                    if v["on"] as? Bool != false { packets.insert((0xd0,[1]),at:0); expected["on"]=true }
                    packets.append((0xd2,[UInt8(b)]))
                } else { packets.append((0xd0,[0])); expected["on"]=false }
            }
            if let on=v["on"] as? Bool {
                if on,setBrightness==0 { fail("INVALID_VALUE","On and zero brightness conflict."); return }
                expected["on"]=on
                if !on { expected.removeValue(forKey:"brightness") }
                if !on { packets.append((0xd0,[0])) }
                else if v["brightness"] == nil { packets.insert((0xd0,[1]),at:0) }
            }
            guard !packets.isEmpty else { fail("INVALID_VALUE","No supported settings supplied."); return }
        }
        phase="verifying"; let id=jobID; let gen=generation
        for (index,item) in packets.enumerated() {
            after(Double(index)*0.15) { if self.jobID==id && self.generation==gen { _=self.send(item.0,item.1) } }
        }
        after(Double(packets.count)*0.15+0.35) {
            if self.jobID==id && self.generation==gen { self.verifyAfter=Date().timeIntervalSince1970; self.queryState() }
        }
    }

    func brightnessLevels() -> [String:Int] {
        guard let data=try? Data(contentsOf:runtime.appendingPathComponent("brightness-levels.json")),
              let levels=(try? JSONSerialization.jsonObject(with:data)) as? [String:Int] else { return [:] }
        return levels
    }
    func previousBrightness() -> Int? {
        guard let id=profile["id"] as? String,let b=brightnessLevels()[id],(1...100).contains(b) else { return nil }
        return b
    }
    func rememberBrightness(_ brightness:Int) {
        guard (1...100).contains(brightness),let id=profile["id"] as? String else { return }
        var levels=brightnessLevels()
        guard levels[id] != brightness else { return }
        levels[id]=brightness
        guard let data=try? JSONSerialization.data(withJSONObject:levels,options:[.sortedKeys]) else { return }
        let path=runtime.appendingPathComponent("brightness-levels.json")
        do {
            try data.write(to:path,options:.atomic)
            try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:path.path)
        } catch { fail("STATE_SAVE_FAILED","Could not save the previous brightness in the private runtime directory.") }
    }

    func beginFade() {
        guard phase=="fadeBaseline" else { return }
        let dur=(values()["duration"] as? Double) ?? (values()["duration"] as? NSNumber)?.doubleValue
        guard let duration=dur else { return }
        let restore=values()["restore_brightness"] as? Bool == true
        let b=(values()["brightness"] as? Int) ?? (values()["brightness"] as? NSNumber)?.intValue
        let target=restore ? previousBrightness() ?? 100 : b
        guard let desired=target else { return }
        let id=jobID
        let isBackground=values()["background"] as? Bool == true
        phase="fading"
        fadeVerifying=false; fadeVerifyAfter=Double.greatestFiniteMagnitude
        fadeActivity=ProcessInfo.processInfo.beginActivity(options:.userInitiated,reason:"Cync fade")
        let start=state["brightness"] as? Int ?? 0,steps=max(2,min(2000,Int(duration/0.15)))
        let started=ProcessInfo.processInfo.systemUptime
        let fadeDeadline = !isBackground && deadline>started ? deadline : started+min(700,max(1,duration+35))
        fadeGeneration+=1
        let currentFadeGen=fadeGeneration
        activeFade=ActiveFade(operationId:id,isBackground:isBackground,target:desired,duration:duration,startedUptime:started,deadline:fadeDeadline,profileId:profile["id"] as? String)
        guard saveFadeStatus(["operation_id":id,"status":"running"]) else {
            fail("STATE_SAVE_FAILED","Could not save the fade operation before starting."); return
        }
        if isBackground {
            writeResponse(["schema_version":1,"request_id":id,"ok":true,"connected":ready,"accepted":true,"verified":false,"operation_id":id])
            job=nil; expected=[:]
        }
        if desired>0 && state["on"] as? Bool != true { _=send(0xd0,[1]) }
        fadeTimer=Timer.scheduledTimer(withTimeInterval:duration/Double(steps),repeats:true) { timer in
            guard self.fadeGeneration==currentFadeGen else { timer.invalidate(); return }
            let progress=min(1,max(0,(ProcessInfo.processInfo.systemUptime-started)/duration))
            if progress>=1 {
                if desired>0 { _=self.send(0xd2,[UInt8(desired)]) } else { _=self.send(0xd0,[0]) }
                timer.invalidate(); self.fadeTimer=nil
                self.fadeVerifying=true
                self.expected=["brightness":desired,"on":desired>0]
                let gen=self.fadeGeneration
                self.after(0.4) {
                    if self.fadeGeneration==gen {
                        self.fadeVerifyAfter=Date().timeIntervalSince1970
                        self.verifyAfter=self.fadeVerifyAfter
                        self.phase="verifying"
                        self.queryState()
                    }
                }
            } else if start>0 || desired>0 {
                let b=max(1,min(100,Int((Double(start)+Double(desired-start)*progress).rounded())))
                _=self.send(0xd2,[UInt8(b)])
            }
        }
    }

    func after(_ delay:Double,_ callback:@escaping ()->Void) { DispatchQueue.main.asyncAfter(deadline:.now()+delay,execute:callback) }
    func endFadeActivity() {
        if let activity=fadeActivity { ProcessInfo.processInfo.endActivity(activity); fadeActivity=nil }
    }
    func finish(_ extra:[String:Any]) {
        guard job != nil else { return }
        var result:[String:Any]=["schema_version":1,"request_id":jobID,"ok":true,"connected":ready]
        for (k,v) in extra { result[k]=v }
        writeResponse(result); job=nil; expected=[:]; phase="idle"; lastActivity=Date(); endFadeActivity()
    }
    func fail(_ code:String,_ message:String) {
        if let fade=activeFade {
            let fadeId=fade.operationId
            cancelActiveFade(reason:"failed")
            saveFadeStatus(["operation_id":fadeId,"status":"failed","error":["code":code,"message":message]])
            if !fade.isBackground {
                writeResponse(["schema_version":1,"request_id":fadeId,"ok":false,"verified":false,"connected":false,"error":["code":code,"message":message]])
                if jobID==fadeId { job=nil; expected=[:] }
            }
        }
        for obs in observers {
            writeResponse(["schema_version":1,"request_id":obs.requestId,"ok":false,"verified":false,"connected":false,"error":["code":code,"message":message]])
        }
        observers.removeAll()
        guard job != nil else { disconnect(); return }
        let id=jobID; fadeTimer?.invalidate(); endFadeActivity(); job=nil; expected=[:]
        phase="idle"; lastActivity=Date()
        // A fresh off report is a valid connection, not a radio failure.
        if code != "LIGHT_OFF" { disconnect() }
        writeResponse(["schema_version":1,"request_id":id,"ok":false,"verified":false,"connected":ready,"error":["code":code,"message":message]])
    }
    func writeResponse(_ value:[String:Any]) {
        guard let id=value["request_id"] as? String,UUID(uuidString:id) != nil,let data=try? JSONSerialization.data(withJSONObject:value,options:[.sortedKeys]) else { return }
        let path=runtime.appendingPathComponent("response-\(id).json")
        try? data.write(to:path,options:.atomic)
        try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:path.path)
    }
    func disconnect() {
        if let fade=activeFade {
            let id=fade.operationId
            cancelActiveFade(reason:"disconnected")
            saveFadeStatus(["operation_id":id,"status":"failed","error":["code":"DISCONNECTED","message":"The Bluetooth connection ended before verification."]])
            if !fade.isBackground {
                writeResponse(["schema_version":1,"request_id":id,"ok":false,"verified":false,"connected":false,"error":["code":"DISCONNECTED","message":"The Bluetooth connection ended before verification."]])
                if jobID==id { job=nil; expected=[:] }
            }
        }
        for obs in observers {
            writeResponse(["schema_version":1,"request_id":obs.requestId,"ok":false,"verified":false,"connected":false,"error":["code":"DISCONNECTED","message":"The Bluetooth connection ended before verification."]])
        }
        observers.removeAll()
        fadeTimer?.invalidate(); fadeTimer=nil; endFadeActivity(); central?.stopScan()
        queuedPeer=nil
        let old=peer; peer=nil
        if let p=old,p.state != .disconnected { disconnecting.insert(p.identifier); central?.cancelPeripheralConnection(p) }
        ready=false; session=nil; credential=nil; pair=nil; control=nil; notify=nil; handshaking=false
    }
    func applicationWillTerminate(_ notification:Notification) { disconnect() }
}
#if !NATIVE_TESTS
let helper=Helper(),application=NSApplication.shared
application.delegate=helper; application.setActivationPolicy(.accessory); application.run()
#endif
