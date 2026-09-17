import QtQuick

Rectangle {
    id: root
    property string message: ""
    signal activated()

    width: parent ? parent.width : 280
    visible: message.length > 0
    implicitHeight: bannerText.implicitHeight + 16
    height: visible ? implicitHeight : 0
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
    MouseArea {
        anchors.fill: parent
        enabled: !bridge.active && !bridge.busy
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.activated()
    }
}
