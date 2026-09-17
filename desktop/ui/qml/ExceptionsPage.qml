import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

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

                Rectangle {
                    visible: bridge.active || bridge.busy
                    width: parent.width
                    height: visible ? bannerText.implicitHeight + 16 : 0
                    radius: 10
                    color: T.toastWarn
                    clip: true
                    Text {
                        id: bannerText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 10
                        text: "Правила применятся при следующем подключении."
                        color: T.warn
                        font.pixelSize: 12
                        font.family: T.fontUi
                        wrapMode: Text.WordWrap
                    }
                }

                Card {
                    width: parent.width
                    implicitHeight: directEd.implicitHeight + 28
                    ExceptionEditor {
                        id: directEd
                        width: parent.width - 28
                        x: 14
                        y: 14
                        title: "Напрямую"
                        subtitle: "Идут мимо VPN."
                        examples: "*.local, 10.0.0.0/8, chrome.exe"
                        settingKey: "routeDirect"
                        onPickRequested: (kind) => {
                            root.pickerTarget = "routeDirect"
                            picker.open(kind)
                        }
                    }
                }

                Card {
                    width: parent.width
                    implicitHeight: vpnEd.implicitHeight + 28
                    ExceptionEditor {
                        id: vpnEd
                        width: parent.width - 28
                        x: 14
                        y: 14
                        title: "Только VPN"
                        subtitle: "Всегда через VPN."
                        examples: "github.com, rustdesk.exe"
                        settingKey: "routeVpn"
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

    onVisibleChanged: if (visible) picker.prefetch()

    Connections {
        target: bridge
        function onPageChanged() {
            if (bridge.page === "exceptions")
                picker.prefetch()
        }
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
        function onProcessListReady(payload) { picker.applyList(payload, "process") }
        function onServiceListReady(payload) { picker.applyList(payload, "service") }
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
