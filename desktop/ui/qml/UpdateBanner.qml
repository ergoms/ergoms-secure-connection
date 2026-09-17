import QtQuick
import QtQuick.Layouts
import "Theme.js" as T

Rectangle {
    id: root
    property string version: ""
    signal updateClicked()

    width: parent ? parent.width : 280
    visible: version.length > 0
    implicitHeight: 52
    radius: 10
    color: "#16352d"
    border.width: 1
    border.color: Qt.rgba(45 / 255, 212 / 255, 168 / 255, 0.35)
    clip: true

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 8
        anchors.topMargin: 8
        anchors.bottomMargin: 8
        spacing: 8

        Text {
            Layout.fillWidth: true
            text: "Доступна версия " + root.version
            color: T.accent
            font.pixelSize: 12
            font.family: T.fontUi
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }

        PrimaryButton {
            Layout.preferredWidth: 108
            Layout.preferredHeight: 34
            implicitHeight: 34
            text: "Обновить"
            primary: true
            enabled: !bridge.busy
            onClicked: root.updateClicked()
        }
    }
}
