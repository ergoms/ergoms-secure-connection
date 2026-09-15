import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    function acceptPicked(item) {
        var editor = pickerTarget === "routeVpn" ? vpnEd : directEd
        if (!item)
            return
        if (item.kind === "service") {
            if (!item.shared && item.name)
                editor.addToken("svc:" + item.name)
            editor.analyzing = true
            bridge.collectProcessPeers("svc:" + item.name)
            return
        }
        var token = item.path ? ("exe:" + item.path) : ("exe:" + (item.name || ""))
        if (!bridge.tokenShared(token) && token !== "exe:")
            editor.addToken(token)
        editor.analyzing = true
        if (item.pid)
            bridge.collectProcessPeers("pid:" + item.pid)
        else
            bridge.collectProcessPeers(token)
    }

    property string pickerTarget: "routeDirect"

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: form.height
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: AppScrollBar {}

            Column {
                id: form
                width: flick.width - 28
                x: 14
                spacing: 10
                topPadding: 10
                bottomPadding: 16

                Text {
                    width: parent.width
                    visible: bridge.active || bridge.busy
                    text: "Правила применятся при следующем подключении."
                    color: T.warn
                    font.pixelSize: 12
                    font.family: T.fontUi
                    wrapMode: Text.WordWrap
                }

                Card {
                    width: parent.width
                    implicitHeight: directEd.implicitHeight + 24
                    ExceptionEditor {
                        id: directEd
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        title: "Напрямую"
                        subtitle: "Эти адреса и программы идут мимо VPN."
                        settingKey: "routeDirect"
                        onPickRequested: (kind) => {
                            root.pickerTarget = "routeDirect"
                            picker.open(kind)
                        }
                    }
                }

                Card {
                    width: parent.width
                    implicitHeight: vpnEd.implicitHeight + 24
                    ExceptionEditor {
                        id: vpnEd
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        title: "Только VPN"
                        subtitle: "Всегда через туннель, даже если правило выше отправило бы напрямую."
                        settingKey: "routeVpn"
                        presetEnabled: true
                        onPickRequested: (kind) => {
                            root.pickerTarget = "routeVpn"
                            picker.open(kind)
                        }
                    }
                }
            }
        }
    }

    ProcessPicker {
        id: picker
        onPicked: (item) => root.acceptPicked(item)
        onBrowseRequested: bridge.browseExecutable()
    }

    Connections {
        target: bridge
        function onAnalyzeReady(query, payload) {
            var obj = {}
            try { obj = JSON.parse(payload) } catch (e) { return }
            var q = String(query || "").replace(/^\s+|\s+$/g, "")
            var d = String(directEd.query || "").replace(/^\s+|\s+$/g, "")
            var v = String(vpnEd.query || "").replace(/^\s+|\s+$/g, "")
            if (q && q === d)
                directEd.applyHint(obj)
            else if (q && q === v)
                vpnEd.applyHint(obj)
        }
        function onProcessListReady(payload) { picker.applyList(payload) }
        function onServiceListReady(payload) { picker.applyList(payload) }
        function onPeersReady(spec, payload) {
            var obj = {}
            try { obj = JSON.parse(payload) } catch (e) { return }
            var editor = root.pickerTarget === "routeVpn" ? vpnEd : directEd
            editor.applyHint(obj)
        }
        function onExecutablePicked(path) {
            picker.close()
            root.acceptPicked({ kind: "process", name: path, path: path, pid: 0 })
        }
    }
}
