import QtQuick

Rectangle {
    id: root

    readonly property string message: {
        if (bridge.page !== "settings" && bridge.page !== "exceptions")
            return ""
        if (bridge.busy)
            return "Дождитесь окончания операции"
        if (bridge.active)
            return "Изменения применятся при следующем подключении"
        return ""
    }

    visible: message.length > 0
    implicitHeight: visible ? bannerText.implicitHeight + 16 : 0
    height: implicitHeight
    radius: 10
    color: T.toastWarn
    clip: true

    Text {
        id: bannerText
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.margins: 10
        text: root.message
        color: T.warn
        font.pixelSize: 12
        font.family: T.fontUi
        wrapMode: Text.WordWrap
    }
}
