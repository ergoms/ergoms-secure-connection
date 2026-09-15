import QtQuick
import QtQuick.Controls
import "Theme.js" as T

Item {
    id: root
    property bool opened: false
    property string kind: "process"
    property var items: []
    property bool loading: false
    signal picked(var item)
    signal browseRequested()

    visible: opened
    anchors.fill: parent
    z: 30

    function open(nextKind) {
        kind = nextKind || "process"
        opened = true
        search.text = ""
        loading = true
        items = []
        if (kind === "service")
            bridge.listServices()
        else
            bridge.listProcesses()
    }

    function close() {
        opened = false
    }

    function applyList(json) {
        try { items = JSON.parse(json || "[]") }
        catch (e) { items = [] }
        loading = false
    }

    readonly property var filtered: {
        var q = search.text.toLowerCase()
        var src = items
        if (!q)
            return src
        var out = []
        for (var i = 0; i < src.length; i++) {
            var it = src[i]
            var hay = [it.name, it.display, it.path, it.exe].join(" ").toLowerCase()
            if (hay.indexOf(q) >= 0)
                out.push(it)
        }
        return out
    }

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.52)
        MouseArea {
            anchors.fill: parent
            onClicked: root.close()
        }
    }

    Rectangle {
        id: sheet
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 10
        height: Math.min(parent.height - 20, 520)
        radius: 16
        color: T.surface
        border.width: 1
        border.color: T.windowBorder
        clip: true

        MouseArea { anchors.fill: parent }

        Column {
            id: header
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: 14
            spacing: 8

            Row {
                width: parent.width
                Text {
                    width: parent.width - 28
                    text: root.kind === "service" ? "Службы Windows" : "Процессы"
                    color: T.text
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    font.family: T.fontUi
                }
                Text {
                    text: "×"
                    color: T.muted
                    font.pixelSize: 18
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -8
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.close()
                    }
                }
            }

            TextField {
                id: search
                width: parent.width
                height: 36
                placeholderText: "Поиск"
                placeholderTextColor: T.muted
                color: T.text
                font.pixelSize: 13
                font.family: T.fontUi
                leftPadding: 10
                background: Rectangle {
                    color: T.surface2
                    radius: 10
                    border.width: 1
                    border.color: search.activeFocus ? T.accent : Qt.rgba(1, 1, 1, 0.04)
                }
            }

            Text {
                visible: root.loading
                height: visible ? implicitHeight : 0
                text: "Собираю список…"
                color: T.muted
                font.pixelSize: 12
                font.family: T.fontUi
            }
        }

        ListView {
            id: list
            anchors.top: header.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: fileBtn.top
            anchors.topMargin: 8
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            anchors.bottomMargin: 8
            clip: true
            model: root.filtered
            spacing: 2
            ScrollBar.vertical: AppScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: list.width
                height: 44
                radius: 10
                color: rowMouse.containsMouse ? T.btn : "transparent"
                Column {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    spacing: 1
                    Text {
                        width: parent.width
                        text: modelData.display || modelData.name || ""
                        color: T.text
                        font.pixelSize: 12
                        font.family: T.fontUi
                        elide: Text.ElideRight
                    }
                    Text {
                        width: parent.width
                        text: {
                            if (modelData.kind === "service")
                                return (modelData.shared ? "общий процесс · " : "") + (modelData.exe || modelData.name)
                            return (modelData.pid ? (modelData.pid + " · ") : "") + (modelData.path || modelData.name)
                        }
                        color: T.muted
                        font.pixelSize: 10
                        font.family: T.fontUi
                        elide: Text.ElideMiddle
                    }
                }
                MouseArea {
                    id: rowMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        root.picked(modelData)
                        root.close()
                    }
                }
            }
        }

        Rectangle {
            id: fileBtn
            visible: root.kind === "process"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 14
            height: visible ? 36 : 0
            radius: 10
            color: fileMouse.containsMouse ? T.btn : T.surface2
            Text {
                visible: root.kind === "process"
                anchors.centerIn: parent
                text: "Выбрать файл…"
                color: T.text
                font.pixelSize: 12
                font.weight: Font.DemiBold
                font.family: T.fontUi
            }
            MouseArea {
                id: fileMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.browseRequested()
            }
        }
    }
}
