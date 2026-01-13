import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import json
from models import (
    CollaborativeSession, SessionStatus, Segmentation,
    User, SegmentationVersion
)
from .segmentation_service import SegmentationService


class SessionService:
    """
    Service for managing collaborative editing sessions.
    Handles session lifecycle and participant management.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def start_session(
        self,
        segmentation_id: int,
        user_id: int,
        session_name: Optional[str] = None
    ) -> CollaborativeSession:
        """
        Start a new collaborative editing session
        
        Args:
            segmentation_id: ID of segmentation to edit
            user_id: ID of user starting the session
            session_name: Optional name for the session
            
        Returns:
            CollaborativeSession record
            
        Raises:
            ValueError: If there's already an active session
        """
        # Check if segmentation exists
        segmentation = self.db.query(Segmentation).get(segmentation_id)
        if not segmentation:
            raise ValueError(f"Segmentation {segmentation_id} not found")
        
        # Check for existing active session
        existing_session = self.db.query(CollaborativeSession).filter(
            CollaborativeSession.segmentation_id == segmentation_id,
            CollaborativeSession.status == SessionStatus.ACTIVE
        ).first()
        
        if existing_session:
            raise ValueError(
                f"Active session already exists for segmentation {segmentation_id}"
            )
        
        # Create new session
        session = CollaborativeSession(
            segmentation_id=segmentation_id,
            started_by_id=user_id,
            status=SessionStatus.ACTIVE,
            session_name=session_name,
            participants_json=json.dumps([user_id])  # Creator is first participant
        )
        
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        
        return session
    
    def end_session(
        self,
        session_id: int,
        user_id: int,
        create_final_version: bool = True
    ) -> CollaborativeSession:
        """
        End a collaborative editing session
        
        Args:
            session_id: ID of session to end
            user_id: ID of user ending the session
            create_final_version: Whether to create a final version
            
        Returns:
            Updated CollaborativeSession record
            
        Raises:
            ValueError: If session not found or already ended
        """
        session = self.db.query(CollaborativeSession).get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.status != SessionStatus.ACTIVE:
            raise ValueError(f"Session {session_id} is not active")
        
        # Only session creator or participants can end it
        participants = json.loads(session.participants_json or "[]")
        if user_id != session.started_by_id and user_id not in participants:
            raise ValueError(f"User {user_id} cannot end this session")
        
        # Create final version if requested
        final_version_id = None
        if create_final_version:
            from services.segmentation_service import SegmentationService
            seg_service = SegmentationService(self.db)
            
            # Reconstruct final state from deltas
            final_data = seg_service.reconstruct_from_deltas(
                session.segmentation_id,
                session_id
            )
            
            # Save as final version
            file_obj = BytesIO(final_data)
            _, version = seg_service.save_full_segmentation(
                segmentation_id=session.segmentation_id,
                file_data=file_obj,
                user_id=user_id,
                change_description=f"Final version from session: {session.session_name or session_id}",
                create_version=True,
                session_id=session_id
            )
            
            final_version_id = version.id if version else None
        
        # Update session
        session.status = SessionStatus.ENDED
        session.ended_at = datetime.utcnow()
        session.final_version_id = final_version_id
        
        self.db.commit()
        self.db.refresh(session)
        
        return session
    
    def add_participant(
        self,
        session_id: int,
        user_id: int
    ) -> CollaborativeSession:
        """
        Add a participant to an active session
        
        Args:
            session_id: ID of session
            user_id: ID of user to add
            
        Returns:
            Updated CollaborativeSession record
        """
        session = self.db.query(CollaborativeSession).get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.status != SessionStatus.ACTIVE:
            raise ValueError(f"Cannot add participant to inactive session")
        
        # Get current participants
        participants = json.loads(session.participants_json or "[]")
        
        # Add user if not already in list
        if user_id not in participants:
            participants.append(user_id)
            session.participants_json = json.dumps(participants)
            self.db.commit()
            self.db.refresh(session)
        
        return session
    
    def remove_participant(
        self,
        session_id: int,
        user_id: int
    ) -> CollaborativeSession:
        """
        Remove a participant from a session
        
        Args:
            session_id: ID of session
            user_id: ID of user to remove
            
        Returns:
            Updated CollaborativeSession record
        """
        session = self.db.query(CollaborativeSession).get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        # Cannot remove the session creator
        if user_id == session.started_by_id:
            raise ValueError("Cannot remove session creator")
        
        # Get current participants
        participants = json.loads(session.participants_json or "[]")
        
        # Remove user if in list
        if user_id in participants:
            participants.remove(user_id)
            session.participants_json = json.dumps(participants)
            self.db.commit()
            self.db.refresh(session)
        
        return session
    
    def get_active_sessions(
        self,
        segmentation_id: Optional[int] = None,
        user_id: Optional[int] = None
    ) -> List[CollaborativeSession]:
        """
        Get active collaborative sessions
        
        Args:
            segmentation_id: Optional filter by segmentation
            user_id: Optional filter by participant
            
        Returns:
            List of active CollaborativeSession records
        """
        query = self.db.query(CollaborativeSession).filter(
            CollaborativeSession.status == SessionStatus.ACTIVE
        )
        
        if segmentation_id:
            query = query.filter(
                CollaborativeSession.segmentation_id == segmentation_id
            )
        
        if user_id:
            # Filter sessions where user is a participant
            all_sessions = query.all()
            filtered_sessions = []
            for session in all_sessions:
                participants = json.loads(session.participants_json or "[]")
                if user_id in participants or user_id == session.started_by_id:
                    filtered_sessions.append(session)
            return filtered_sessions
        
        return query.all()
    
    def get_session_participants(
        self,
        session_id: int
    ) -> List[User]:
        """
        Get all participants in a session
        
        Args:
            session_id: ID of session
            
        Returns:
            List of User records
        """
        session = self.db.query(CollaborativeSession).get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        participant_ids = json.loads(session.participants_json or "[]")
        
        return self.db.query(User).filter(
            User.id.in_(participant_ids)
        ).all()
    
    def is_user_in_session(
        self,
        session_id: int,
        user_id: int
    ) -> bool:
        """
        Check if user is a participant in session
        
        Args:
            session_id: ID of session
            user_id: ID of user
            
        Returns:
            bool: True if user is in session
        """
        session = self.db.query(CollaborativeSession).get(session_id)
        if not session:
            return False
        
        participants = json.loads(session.participants_json or "[]")
        return user_id in participants or user_id == session.started_by_id


