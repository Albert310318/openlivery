package main

import (
	"context"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
)

type echoingSender struct {
	t       *testing.T
	manager *manager
	ids     []string
	count   int
}

func (s *echoingSender) GenerateMessageID() types.MessageID {
	id := s.ids[s.count]
	s.count++
	return id
}

func (s *echoingSender) SendMessage(_ context.Context, _ types.JID, _ *waE2E.Message, extras ...whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
	id := extras[0].ID
	if !s.manager.echoes.sentByUs("channel", id) {
		s.t.Fatal("an immediate echo was not registered before transmission")
	}
	return whatsmeow.SendResponse{ID: id}, nil
}

func TestOwnAudioAndFollowupAreTrackedBeforeAcknowledgement(t *testing.T) {
	m := &manager{echoes: newEchoSet()}
	sender := &echoingSender{t: t, manager: m, ids: []string{"audio", "caption"}}
	jid := types.NewJID("573001112233", types.DefaultUserServer)
	for range sender.ids {
		if _, err := m.sendTracked(context.Background(), "channel", sender, jid, &waE2E.Message{}); err != nil {
			t.Fatal(err)
		}
	}
	if !m.echoes.sentByUs("channel", "audio") || !m.echoes.sentByUs("channel", "caption") {
		t.Fatal("every outgoing message must be distinguishable from a phone reply")
	}
}
