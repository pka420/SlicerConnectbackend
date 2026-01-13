from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import BinaryIO, List, Dict, Optional, Tuple
from datetime import datetime
from io import BytesIO
import json

from models import (
    Segmentation, SegmentationVersion, SegmentationEdit, 
    EditType, User, CollaborativeSession
)
from api.storage_service import get_storage_service
from .delta_manager import DeltaManager


class SegmentationService:
    """
    Service for managing segmentation operations.
    Handles both REST (full saves) and WebSocket (deltas) workflows.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.storage = get_storage_service()
        self.delta_manager = DeltaManager()
    
    def save_full_segmentation(
        self,
        segmentation_id: int,
        file_data: BinaryIO,
        user_id: int,
        change_description: Optional[str] = None,
        create_version: bool = True,
        session_id: Optional[int] = None
    ) -> Tuple[SegmentationEdit, Optional[SegmentationVersion]]:
        """
        Save complete segmentation file (.nrrd)
        
        Args:
            segmentation_id: ID of segmentation
            file_data: Binary file data (.nrrd file)
            user_id: ID of user saving
            change_description: Optional description of changes
            create_version: Whether to create a new version entry
            session_id: Optional collaborative session ID
            
        Returns:
            Tuple of (SegmentationEdit, SegmentationVersion or None)
        """
        # Get segmentation
        segmentation = self.db.query(Segmentation).filter(
            Segmentation.id == segmentation_id
        ).first()
        
        if not segmentation:
            raise ValueError(f"Segmentation {segmentation_id} not found")
        
        # Save file to storage
        file_path = self.storage.save_file(
            file_data=file_data,
            file_type='nrrd',
            segmentation_id=segmentation_id,
            metadata={'user_id': user_id, 'type': 'full_save'}
        )
        
        file_size = self.storage.get_file_size(file_path)
        
        # Create edit record
        edit = SegmentationEdit(
            segmentation_id=segmentation_id,
            edit_type=EditType.FULL_SAVE,
            file_path=file_path,
            data_size_bytes=file_size,
            created_by_id=user_id,
            session_id=session_id,
            change_description=change_description
        )
        self.db.add(edit)
        
        # Update segmentation metadata
        segmentation.updated_at = datetime.utcnow()
        segmentation.last_editor_id = user_id
        
        # Create version if requested
        version = None
        if create_version:
            version = self.create_version(
                segmentation_id=segmentation_id,
                user_id=user_id,
                file_path=file_path,
                change_description=change_description,
                is_complete_state=True
            )
        
        self.db.commit()
        self.db.refresh(edit)
        if version:
            self.db.refresh(version)
        
        return edit, version
    
    def apply_delta(
        self,
        segmentation_id: int,
        delta: Dict,
        user_id: int,
        session_id: Optional[int] = None
    ) -> SegmentationEdit:
        """
        Apply incremental change (delta) to segmentation
        Used during real-time collaborative editing
        
        Args:
            segmentation_id: ID of segmentation
            delta: Delta object with voxel changes
                Example: {
                    "action": "paint",
                    "voxel_changes": [{"x": 120, "y": 45, "z": 78, "old": 0, "new": 1}],
                    "metadata": {...}
                }
            user_id: ID of user making changes
            session_id: Collaborative session ID
            
        Returns:
            SegmentationEdit record
        """
        # Get segmentation
        segmentation = self.db.query(Segmentation).filter(
            Segmentation.id == segmentation_id
        ).first()
        
        if not segmentation:
            raise ValueError(f"Segmentation {segmentation_id} not found")
        
        # Encode delta
        delta_str, size = self.delta_manager.encode_delta(delta, compress=True)
        
        # Determine storage method
        file_path = None
        delta_data = None
        
        if size < DeltaManager.INLINE_DELTA_MAX_SIZE:
            # Store inline in database
            delta_data = delta_str
        else:
            # Save as file (rare for large deltas)
            file_obj = BytesIO(delta_str.encode('utf-8'))
            file_path = self.storage.save_file(
                file_data=file_obj,
                file_type='delta',
                segmentation_id=segmentation_id
            )
        
        # Create edit record
        edit = SegmentationEdit(
            segmentation_id=segmentation_id,
            edit_type=EditType.DELTA,
            file_path=file_path,
            delta_data=delta_data,
            data_size_bytes=size,
            created_by_id=user_id,
            session_id=session_id,
            voxels_modified=delta.get('voxel_count', len(delta.get('voxel_changes', []))),
            change_description=delta.get('metadata', {}).get('description')
        )
        self.db.add(edit)
        
        # Update segmentation metadata
        segmentation.updated_at = datetime.utcnow()
        segmentation.last_editor_id = user_id
        
        self.db.commit()
        self.db.refresh(edit)
        
        # Check if snapshot is needed
        if session_id:
            self._check_and_create_snapshot(segmentation_id, session_id, user_id)
        
        return edit
    
    def create_version(
        self,
        segmentation_id: int,
        user_id: int,
        file_path: str,
        change_description: Optional[str] = None,
        is_complete_state: bool = True
    ) -> SegmentationVersion:
        """
        Create a new version entry for segmentation
        
        Args:
            segmentation_id: ID of segmentation
            user_id: ID of user creating version
            file_path: Path to version file
            change_description: Optional description
            is_complete_state: Whether this is a complete state or delta
            
        Returns:
            SegmentationVersion record
        """
        # Get current max version number
        max_version = self.db.query(SegmentationVersion).filter(
            SegmentationVersion.segmentation_id == segmentation_id
        ).order_by(desc(SegmentationVersion.version_number)).first()
        
        next_version = 1 if not max_version else max_version.version_number + 1
        
        # Create version
        version = SegmentationVersion(
            segmentation_id=segmentation_id,
            version_number=next_version,
            created_by_id=user_id,
            change_description=change_description,
            file_path=file_path,
            is_complete_state=is_complete_state
        )
        
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        
        return version
    
    def get_segmentation_data(
        self,
        segmentation_id: int,
        version_id: Optional[int] = None
    ) -> bytes:
        """
        Get segmentation file data
        
        Args:
            segmentation_id: ID of segmentation
            version_id: Optional specific version ID (defaults to latest)
            
        Returns:
            bytes: Segmentation file data
        """
        if version_id:
            # Get specific version
            version = self.db.query(SegmentationVersion).filter(
                SegmentationVersion.id == version_id,
                SegmentationVersion.segmentation_id == segmentation_id
            ).first()
            
            if not version:
                raise ValueError(f"Version {version_id} not found")
            
            file_path = version.file_path
        else:
            # Get latest full save
            latest_edit = self.db.query(SegmentationEdit).filter(
                SegmentationEdit.segmentation_id == segmentation_id,
                SegmentationEdit.edit_type.in_([EditType.FULL_SAVE, EditType.SNAPSHOT])
            ).order_by(desc(SegmentationEdit.created_at)).first()
            
            if not latest_edit:
                raise ValueError(f"No data found for segmentation {segmentation_id}")
            
            file_path = latest_edit.file_path
        
        # Read and return file data
        return self.storage.get_file(file_path)
    
    def get_version_history(
        self,
        segmentation_id: int,
        limit: Optional[int] = None
    ) -> List[SegmentationVersion]:
        """
        Get version history for a segmentation
        
        Args:
            segmentation_id: ID of segmentation
            limit: Optional limit on number of versions to return
            
        Returns:
            List of SegmentationVersion records, ordered by version number (newest first)
        """
        query = self.db.query(SegmentationVersion).filter(
            SegmentationVersion.segmentation_id == segmentation_id
        ).order_by(desc(SegmentationVersion.version_number))
        
        if limit:
            query = query.limit(limit)
        
        return query.all()
    
    def get_edits_since(
        self,
        segmentation_id: int,
        since_timestamp: datetime,
        session_id: Optional[int] = None
    ) -> List[SegmentationEdit]:
        """
        Get all edits since a specific timestamp
        Useful for syncing and conflict resolution
        
        Args:
            segmentation_id: ID of segmentation
            since_timestamp: Get edits after this time
            session_id: Optional filter by session
            
        Returns:
            List of SegmentationEdit records
        """
        query = self.db.query(SegmentationEdit).filter(
            SegmentationEdit.segmentation_id == segmentation_id,
            SegmentationEdit.created_at > since_timestamp
        )
        
        if session_id:
            query = query.filter(SegmentationEdit.session_id == session_id)
        
        return query.order_by(SegmentationEdit.created_at).all()
    
    def reconstruct_from_deltas(
        self,
        segmentation_id: int,
        session_id: int
    ) -> bytes:
        """
        Reconstruct current segmentation state from base + deltas
        Used when session ends to create final version
        
        Args:
            segmentation_id: ID of segmentation
            session_id: Session ID to reconstruct from
            
        Returns:
            bytes: Reconstructed .nrrd file data
        """
        # Get last snapshot or full save before session started
        session = self.db.query(CollaborativeSession).get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        base_edit = self.db.query(SegmentationEdit).filter(
            SegmentationEdit.segmentation_id == segmentation_id,
            SegmentationEdit.edit_type.in_([EditType.FULL_SAVE, EditType.SNAPSHOT]),
            SegmentationEdit.created_at <= session.started_at
        ).order_by(desc(SegmentationEdit.created_at)).first()
        
        if not base_edit:
            raise ValueError(f"No base state found for reconstruction")
        
        # Load base array
        import nrrd
        base_data = self.storage.get_file(base_edit.file_path)
        base_array, header = nrrd.reads(base_data)
        
        # Get all deltas in session
        deltas_edits = self.db.query(SegmentationEdit).filter(
            SegmentationEdit.segmentation_id == segmentation_id,
            SegmentationEdit.session_id == session_id,
            SegmentationEdit.edit_type == EditType.DELTA
        ).order_by(SegmentationEdit.created_at).all()
        
        # Apply deltas
        for edit in deltas_edits:
            delta_str = edit.delta_data or self.storage.get_file(edit.file_path).decode('utf-8')
            delta = self.delta_manager.decode_delta(delta_str)
            self.delta_manager.apply_delta_to_array(base_array, delta)
        
        # Convert back to .nrrd bytes
        output = BytesIO()
        nrrd.write(output, base_array, header)
        return output.getvalue()
    
    def _check_and_create_snapshot(
        self,
        segmentation_id: int,
        session_id: int,
        user_id: int
    ):
        """
        Check if snapshot should be created and create it if needed
        Internal method called after applying deltas
        """
        # Count deltas since last snapshot in this session
        last_snapshot = self.db.query(SegmentationEdit).filter(
            SegmentationEdit.segmentation_id == segmentation_id,
            SegmentationEdit.session_id == session_id,
            SegmentationEdit.edit_type == EditType.SNAPSHOT
        ).order_by(desc(SegmentationEdit.created_at)).first()
        
        if last_snapshot:
            deltas_since = self.db.query(SegmentationEdit).filter(
                SegmentationEdit.segmentation_id == segmentation_id,
                SegmentationEdit.session_id == session_id,
                SegmentationEdit.edit_type == EditType.DELTA,
                SegmentationEdit.created_at > last_snapshot.created_at
            ).count()
            
            time_since = (datetime.utcnow() - last_snapshot.created_at).total_seconds() / 60
        else:
            # No snapshot yet, count all deltas
            deltas_since = self.db.query(SegmentationEdit).filter(
                SegmentationEdit.segmentation_id == segmentation_id,
                SegmentationEdit.session_id == session_id,
                SegmentationEdit.edit_type == EditType.DELTA
            ).count()
            
            session = self.db.query(CollaborativeSession).get(session_id)
            time_since = (datetime.utcnow() - session.started_at).total_seconds() / 60
        
        # Check if snapshot is needed
        if self.delta_manager.should_create_snapshot(deltas_since, time_since):
            # Reconstruct and save snapshot
            reconstructed_data = self.reconstruct_from_deltas(segmentation_id, session_id)
            
            file_obj = BytesIO(reconstructed_data)
            file_path = self.storage.save_file(
                file_data=file_obj,
                file_type='snapshot',
                segmentation_id=segmentation_id
            )
            
            snapshot_edit = SegmentationEdit(
                segmentation_id=segmentation_id,
                edit_type=EditType.SNAPSHOT,
                file_path=file_path,
                data_size_bytes=len(reconstructed_data),
                created_by_id=user_id,
                session_id=session_id,
                change_description="Automatic snapshot"
            )
            self.db.add(snapshot_edit)
            self.db.commit()
