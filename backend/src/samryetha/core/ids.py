"""Nominal identifiers shared across typed backend domains."""

from typing import NewType


UserID = NewType("UserID", int)
BoardID = NewType("BoardID", int)
DraftID = NewType("DraftID", int)
AttachmentID = NewType("AttachmentID", int)
ConversationID = NewType("ConversationID", int)
MessageID = NewType("MessageID", int)
DiscussionID = NewType("DiscussionID", int)
ReplyID = NewType("ReplyID", int)
NotificationID = NewType("NotificationID", int)
OutboxEventID = NewType("OutboxEventID", int)
FeedbackProjectID = NewType("FeedbackProjectID", int)
FeedbackItemID = NewType("FeedbackItemID", int)
FeedbackCommentID = NewType("FeedbackCommentID", int)
FeedbackAPIKeyID = NewType("FeedbackAPIKeyID", int)
TaskID = NewType("TaskID", int)
TaskCommentID = NewType("TaskCommentID", int)
ReportID = NewType("ReportID", int)
ModerationActionID = NewType("ModerationActionID", int)
