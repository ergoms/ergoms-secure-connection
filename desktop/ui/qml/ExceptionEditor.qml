import QtQuick
import QtQuick.Controls
import "Theme.js" as T

Column {
    id: root
    property string title: ""
    property string subtitle: ""
    property string settingKey: ""
    property bool presetEnabled: false
    property bool presetExpanded: false
    property bool listOpen: true
    property var hint: ({})
    property bool analyzing: false
    property alias query: input.text
    signal pickRequested(string kind)

    width: parent ? parent.width : 280
    spacing: 8
    clip: true

    readonly property string rawJson: root.settingKey === "routeVpn"
        ? String(bridge.settings.routeVpn || "[]")
        : String(bridge.settings.routeDirect || "[]")
    readonly property var listItems: {
        var _watch = rawJson
        var _exp = presetExpanded
        return chipModel()
    }
    readonly property int listCount: {
        var _watch = rawJson
        return tokens().length
    }

    readonly property var presetList: {
        try { return JSON.parse(bridge.vpnPresetJson || "[]") }
        catch (e) { return [] }
    }

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
        var t = String(raw || "").replace(/^\s+|\s+$/g, "")
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

    function removePreset() {
        var set = {}
        var preset = root.presetList
        for (var i = 0; i < preset.length; i++)
            set[String(preset[i]).toLowerCase()] = true
        setTokens(tokens().filter(function (t) { return !set[String(t).toLowerCase()] }))
        root.presetExpanded = false
    }

    function hasFullPreset() {
        if (!root.presetEnabled)
            return false
        var cur = {}
        var list = tokens()
        for (var i = 0; i < list.length; i++)
            cur[String(list[i]).toLowerCase()] = true
        var preset = root.presetList
        if (!preset.length)
            return false
        for (var j = 0; j < preset.length; j++) {
            if (!cur[String(preset[j]).toLowerCase()])
                return false
        }
        return true
    }

    function chipModel() {
        var list = tokens()
        if (!root.presetEnabled || !hasFullPreset() || root.presetExpanded)
            return list
        var set = {}
        var preset = root.presetList
        for (var i = 0; i < preset.length; i++)
            set[String(preset[i]).toLowerCase()] = true
        var extra = list.filter(function (t) { return !set[String(t).toLowerCase()] })
        return ["__preset__"].concat(extra)
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
        text: root.title
        color: T.text
        font.pixelSize: 14
        font.weight: Font.DemiBold
        font.family: T.fontUi
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
                group: raw === "__preset__"
                kind: group ? "domain" : bridge.tokenKind(raw)
                label: group ? ("GitHub + Cursor · " + root.presetList.length) : bridge.tokenLabel(raw)
                onRemoveRequested: {
                    if (group)
                        root.removePreset()
                    else
                        root.removeToken(raw)
                }
                onClicked: {
                    if (group)
                        root.presetExpanded = true
                }
            }
        }
    }

    TextField {
        id: input
        width: parent.width
        height: 38
        placeholderText: "домен, IP, *.suffix или name.exe"
        placeholderTextColor: T.muted
        color: T.text
        selectedTextColor: T.text
        selectionColor: T.select
        font.pixelSize: 13
        font.family: T.fontUi
        leftPadding: 12
        rightPadding: 12
        verticalAlignment: TextInput.AlignVCenter
        onTextEdited: analyzeTimer.restart()
        onAccepted: {
            var q = text.replace(/^\s+|\s+$/g, "")
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
            border.color: input.activeFocus ? T.accent : Qt.rgba(1, 1, 1, 0.04)
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
                border.color: Qt.rgba(1, 1, 1, 0.05)
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
