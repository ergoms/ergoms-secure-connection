import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Theme.js" as T

Item {
    id: root

    function timeOf(line) {
        return line.length >= 8 ? line.substring(0, 8) : ""
    }

    function msgOf(line) {
        return line.length > 10 ? line.substring(10) : line
    }

    function tone(msg) {
        var s = String(msg).toLowerCase()
        if (/ошибка|error|fail|не ок|не остановил|deadline|не удалось/.test(s))
            return T.danger
        if (/warn|не видны|пропал/.test(s))
            return T.warn
        if (/\bok\b|готов|слушает|сохранен|скопирован/.test(s))
            return T.accent
        return T.muted
    }

    function reloadAll() {
        logModel.clear()
        var raw = bridge.logText
        if (!raw || !raw.length)
            return
        var lines = raw.split("\n")
        for (var i = 0; i < lines.length; ++i) {
            if (lines[i].length)
                logModel.append({ line: lines[i] })
        }
        Qt.callLater(function () {
            if (logList.count)
                logList.positionViewAtEnd()
        })
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 14
        color: T.surface
        radius: 14
        border.color: Qt.rgba(1, 1, 1, 0.04)
        border.width: 1
        clip: true

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 44
                color: "transparent"

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Журнал"
                    color: T.text
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.2
                    font.family: T.fontUi
                }

                Rectangle {
                    id: copyBtn
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: copyLabel.implicitWidth + 18
                    height: 28
                    radius: 8
                    color: copyMouse.containsMouse ? T.btn : "transparent"
                    border.width: 1
                    border.color: Qt.rgba(1, 1, 1, 0.08)
                    Text {
                        id: copyLabel
                        anchors.centerIn: parent
                        text: "Копировать"
                        color: copyMouse.containsMouse ? T.text : T.muted
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        font.family: T.fontUi
                    }
                    MouseArea {
                        id: copyMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: bridge.copyLog()
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: Qt.rgba(1, 1, 1, 0.04)
            }

            ToastHost {
                id: logToast
                overlay: false
                Layout.fillWidth: true
                Layout.leftMargin: 10
                Layout.rightMargin: 10
                Layout.topMargin: shown ? 8 : 0
                Layout.bottomMargin: shown ? 4 : 0
            }

            ListView {
                id: logList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                spacing: 0
                model: ListModel { id: logModel }
                ScrollBar.vertical: AppScrollBar {}

                header: Item { height: 8; width: 1 }
                footer: Item { height: 10; width: 1 }

                delegate: Item {
                    id: rowWrap
                    width: logList.width
                    height: row.implicitHeight + 8
                    readonly property string lineText: model.line

                    Row {
                        id: row
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.leftMargin: 12
                        anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 10

                        Text {
                            width: 54
                            text: root.timeOf(rowWrap.lineText)
                            color: T.muted
                            font.pixelSize: 11
                            font.family: T.fontMono
                        }

                        Text {
                            width: parent.width - 64
                            text: root.msgOf(rowWrap.lineText)
                            color: root.tone(root.msgOf(rowWrap.lineText))
                            wrapMode: Text.Wrap
                            font.pixelSize: 12
                            font.family: T.fontUi
                            lineHeight: 1.25
                        }
                    }
                }

                Text {
                    visible: logModel.count === 0
                    anchors.centerIn: parent
                    text: "Пока пусто"
                    color: T.muted
                    font.pixelSize: 13
                    font.family: T.fontUi
                }
            }
        }
    }

    Component.onCompleted: reloadAll()

    Connections {
        target: bridge
        function onToast(message, kind) {
            if (bridge.page === "log")
                logToast.show(message, kind)
        }
        function onLogAppended(line) {
            if (logModel.count > 1800)
                root.reloadAll()
            else if (line && line.length)
                logModel.append({ line: line })
            if (logList.atYEnd || logList.contentHeight <= logList.height)
                Qt.callLater(function () { logList.positionViewAtEnd() })
        }
    }
}
