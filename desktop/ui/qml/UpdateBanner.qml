import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    property string version: ""
    property bool checking: false
    signal updateClicked()

    width: parent ? parent.width : 280
    visible: checking || version.length > 0
    implicitHeight: 52
    radius: 10
    color: T.bannerUpdate
    border.width: 1
    border.color: T.bannerUpdateBorder
    clip: true

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 8
        anchors.topMargin: 8
        anchors.bottomMargin: 8
        spacing: 8

        Spinner {
            visible: root.checking
            Layout.preferredWidth: visible ? 18 : 0
            Layout.preferredHeight: 18
        }

        Text {
            Layout.fillWidth: true
            text: root.checking
                  ? "Проверяю обновления…"
                  : ("Доступна версия " + root.version)
            color: T.accent
            font.pixelSize: 12
            font.family: T.fontUi
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
        }

        PrimaryButton {
            visible: !root.checking && root.version.length > 0
            Layout.preferredWidth: visible ? 108 : 0
            Layout.preferredHeight: 34
            implicitHeight: 34
            text: "Обновить"
            primary: true
            enabled: !bridge.busy
            onClicked: root.updateClicked()
        }
    }
}
