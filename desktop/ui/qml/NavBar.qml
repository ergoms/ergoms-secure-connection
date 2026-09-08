import QtQuick
import "Theme.js" as T

Rectangle {
    id: root
    color: T.surface
    height: 56

    Row {
        anchors.fill: parent
        Repeater {
            model: [
                { key: "home", label: "Главная" },
                { key: "settings", label: "Настройки" },
                { key: "log", label: "Журнал" }
            ]
            delegate: Item {
                required property var modelData
                width: root.width / 3
                height: root.height

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 8
                    radius: 10
                    color: bridge.page === modelData.key ? T.btn : "transparent"
                    Behavior on color { ColorAnimation { duration: 150 } }

                    Text {
                        anchors.centerIn: parent
                        text: modelData.label
                        color: bridge.page === modelData.key ? T.text : T.muted
                        font.pixelSize: 13
                        font.bold: true
                        font.family: T.fontUi
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: bridge.setPage(modelData.key)
                }
            }
        }
    }
}
