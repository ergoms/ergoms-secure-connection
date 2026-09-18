import QtQuick
import QtQuick.Controls.Basic

Column {
    id: root
    property string title: ""
    property string subtitle: ""
    property string examples: "github.com, 10.0.0.0/8, chrome.exe"
    property string settingKey: ""
    property var hint: ({})
    property bool analyzing: false
    property alias query: input.text
    signal pickRequested(string kind)

    width: parent ? parent.width : 280
    spacing: 8

    readonly property string rawJson: root.settingKey === "routeVpn"
        ? String(bridge.settings.routeVpn || "[]")
        : String(bridge.settings.routeDirect || "[]")
    readonly property var listItems: {
        var _watch = rawJson
        return tokens()
    }
    readonly property int listCount: {
        var _watch = rawJson
        return tokens().length
    }
    property bool listOpen: true

    function tokens() {
        try { return JSON.parse(String(bridge.settings[root.settingKey] || "[]")) }
        catch (e) { return [] }
    }

    function setTokens(arr) {
        var seen = {}
        var out = []
        for (var i = 0; i < arr.length; i++) {
            var t = String(arr[i] || "").replace(/^\s+|\s+$/g, "")
            if (!t)
                continue
            var key = t.toLowerCase()
            if (seen[key])
                continue
            seen[key] = true
            out.push(t)
        }
        bridge.settings[root.settingKey] = JSON.stringify(out)
        if (root.settingKey === "routeDirect")
            bridge.settings.proxyBypass = out.join(", ")
        saveTimer.restart()
    }

    function addToken(raw) {
        var t = bridge.normalizeRouteToken(String(raw || ""))
        if (!t)
            return
        var cur = tokens()
        cur.push(t)
        setTokens(cur)
        input.text = ""
        root.hint = {}
        root.analyzing = false
        root.listOpen = true
    }

    function removeToken(raw) {
        var want = String(raw || "").toLowerCase()
        setTokens(tokens().filter(function (t) { return String(t).toLowerCase() !== want }))
    }

    function applyHint(obj) {
        root.hint = obj || {}
        root.analyzing = false
    }

    Timer {
        id: saveTimer
        interval: 350
        onTriggered: bridge.saveExceptions()
    }

    Timer {
        id: analyzeTimer
        interval: 350
        onTriggered: {
            var q = input.text.replace(/^\s+|\s+$/g, "")
            if (!q) {
                root.hint = {}
                root.analyzing = false
                return
            }
            root.analyzing = true
            bridge.analyzeToken(q)
        }
    }

    Text {
        width: parent.width
        text: root.title
        color: T.text
        font.pixelSize: 14
        font.weight: Font.DemiBold
        font.family: T.fontUi
        wrapMode: Text.WordWrap
    }
    Text {
        width: parent.width
        text: root.subtitle
        color: T.muted
        font.pixelSize: 11
        font.family: T.fontUi
        wrapMode: Text.WordWrap
    }

    Rectangle {
        width: parent.width
        height: 36
        radius: 10
        color: T.surface2
        Text {
            anchors.left: parent.left
            anchors.leftMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            text: root.listCount ? ("Список · " + root.listCount) : "Список пуст"
            color: T.text
            font.pixelSize: 12
            font.weight: Font.DemiBold
            font.family: T.fontUi
        }
        Text {
            anchors.right: parent.right
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            text: root.listOpen ? "Скрыть" : "Показать"
            color: T.accent
            font.pixelSize: 12
            font.weight: Font.DemiBold
            font.family: T.fontUi
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.listOpen = !root.listOpen
        }
    }

    Column {
        visible: root.listOpen
        width: parent.width
        height: visible ? implicitHeight : 0
        spacing: 6
        Repeater {
            model: root.listItems
            delegate: ExceptionRow {
                required property var modelData
                raw: String(modelData)
                kind: bridge.tokenKind(raw)
                label: bridge.tokenLabel(raw)
                onRemoveRequested: root.removeToken(raw)
            }
        }
    }

    Text {
        width: parent.width
        text: "домен, IP или программа — например " + root.examples
        color: T.muted
        font.pixelSize: 11
        font.weight: Font.Medium
        font.letterSpacing: 0.15
        font.family: T.fontUi
        wrapMode: Text.WordWrap
    }

    TextField {
        id: input
        width: parent.width
        height: 44
        color: T.text
        selectedTextColor: T.text
        selectionColor: T.select
        font.pixelSize: 13
        font.family: T.fontUi
        leftPadding: 14
        rightPadding: 14
        topPadding: 12
        bottomPadding: 12
        verticalAlignment: TextInput.AlignVCenter
        onTextEdited: analyzeTimer.restart()
        onAccepted: {
            var q = bridge.normalizeRouteToken(text)
            if (!q)
                return
            if (bridge.tokenAmbiguous(q)) {
                analyzeTimer.restart()
                return
            }
            root.addToken(q)
        }
        background: Rectangle {
            color: T.surface2
            radius: 10
            border.width: 1
            border.color: input.activeFocus ? T.accent : T.hairline
        }
    }

    AnalyzerHint {
        width: parent.width
        result: root.hint
        busy: root.analyzing
        onAddToken: (token) => root.addToken(token)
    }

    Row {
        spacing: 6
        width: parent.width
        Repeater {
            model: [
                { label: "+ exe", kind: "process" },
                { label: "+ служба", kind: "service" }
            ]
            delegate: Rectangle {
                required property var modelData
                width: (parent.width - 6) / 2
                height: 32
                radius: 9
                color: mouse.containsMouse ? T.btn : T.surface2
                border.width: 1
                border.color: T.hairline
                Text {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: T.text
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    font.family: T.fontUi
                }
                MouseArea {
                    id: mouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.pickRequested(modelData.kind)
                }
            }
        }
    }
}
