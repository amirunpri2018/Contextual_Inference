"""
Mask R-CNN
The main Mask R-CNN model implemenetation.

Copyright (c) 2017 Matterport, Inc.
Licensed under the MIT License (see LICENSE for details)
Written by Waleed Abdulla
"""

import keras.backend as KB
import keras.layers  as KL
from   mrcnn.roialign_layer  import PyramidROIAlign
from   mrcnn.batchnorm_layer import BatchNorm


############################################################
#  FPN Layers Graph - 
############################################################
def fpn_graph(Resnet_Layers):
    """
    #----------------------------------------------------------------------------
    # Build the Feature Pyramid Network (FPN) layers.
    # Top-down Layers
    # Returns a list of the last layers of each stage, 5 in total.
    # Don't create the thead (stage 5), so we pick the 4th item in the list.
    #----------------------------------------------------------------------------        
    # Top-down Layers
    # TODO: add assert to varify feature map sizes match what's in config
    """
    print('\n>>> Feature Pyramid Network (FPN) Graph ')
    
    _, C2, C3, C4, C5 = Resnet_Layers
    
    P5 = KL.Conv2D(256, (1, 1), name='fpn_c5p5')(C5)
    
    P4 = KL.Add(name="fpn_p4add")([
         KL.UpSampling2D(size=(2, 2), name="fpn_p5upsampled")(P5),
         KL.Conv2D(256, (1, 1), name='fpn_c4p4')(C4)])
    
    P3 = KL.Add(name="fpn_p3add")([
         KL.UpSampling2D(size=(2, 2), name="fpn_p4upsampled")(P4),
         KL.Conv2D(256, (1, 1), name='fpn_c3p3')(C3)])
    
    P2 = KL.Add(name="fpn_p2add")([
         KL.UpSampling2D(size=(2, 2), name="fpn_p3upsampled")(P3),
         KL.Conv2D(256, (1, 1), name='fpn_c2p2')(C2)])
    
    # Attach 3x3 conv to all P layers to get the final feature maps.
    P2 = KL.Conv2D(256, (3, 3), padding="SAME", name="fpn_p2")(P2)
    P3 = KL.Conv2D(256, (3, 3), padding="SAME", name="fpn_p3")(P3)
    P4 = KL.Conv2D(256, (3, 3), padding="SAME", name="fpn_p4")(P4)
    P5 = KL.Conv2D(256, (3, 3), padding="SAME", name="fpn_p5")(P5)
    
    # P6 is used for the 5th anchor scale in RPN. Generated by
    # subsampling from P5 with stride of 2.
    P6 = KL.MaxPooling2D(pool_size=(1, 1), strides=2, name="fpn_p6")(P5)
    print('     FPN P2 shape :', KB.int_shape(P2))
    print('     FPN P3 shape :', KB.int_shape(P3))
    print('     FPN P4 shape :', KB.int_shape(P4))
    print('     FPN P5 shape :', KB.int_shape(P5))
    print('     FPN P6 shape :', KB.int_shape(P6))

    return [P2, P3, P4, P5, P6]
    

###############################################################
#  Feature Pyramid Network Head - Classifier
###############################################################
def fpn_classifier_graph(rois, feature_maps, image_shape, pool_size, num_classes):
    '''
    Builds the computation graph of the feature pyramid network classifier
    and regressor heads.
    
    Inputs:
    -------
    rois:               [batch, num_rois, 4 ] 
                        Proposal boxes in normalized coordinates (y1, x1, y2, x2)
                        
    feature_maps:       List of feature maps from diffent layers of the pyramid,
                        [P2, P3, P4, P5]. Each has a different resolution.
    image_shape:        [height, width, depth]
    
    pool_size:          The width of the square feature map generated from ROI Pooling.
    
    num_classes:        number of classes, which determines the depth of the results

    Returns:
    --------
    logits:             [N, NUM_CLASSES] classifier logits (before softmax)
    
    probs:              [N, NUM_CLASSES] classifier probabilities
    
    bbox_deltas:        [N, (dy, dx, log(dh), log(dw))] 
                        Deltas to apply to proposal boxes
                        
    '''
    print('\n>>> FPN Classifier Graph ')
    print('     rois shape          :', rois.get_shape())
    print('     feature_maps :', len(feature_maps))
    for item in feature_maps:
        print('     feature_maps shape  :', item.get_shape())
    print('     input_shape         :', image_shape)
    print('     pool_size           :', pool_size)
    
    # ROI Pooling
    # Shape: [batch, num_boxes, pool_height, pool_width, channels]
    x = PyramidROIAlign([pool_size, pool_size], image_shape,
                            name="roi_align_classifier")([rois] + feature_maps)
    # Two 1024 FC layers (implemented with Conv2D for consistency)
    x = KL.TimeDistributed(KL.Conv2D(1024, (pool_size, pool_size), padding="valid"), name="mrcnn_class_conv1")(x)
    x = KL.TimeDistributed(BatchNorm(axis=3), name='mrcnn_class_bn1')(x)
    x = KL.Activation('relu')(x)
    
    # x = KL.Dropout(0.5)(x)
    x = KL.TimeDistributed(KL.Conv2D(1024, (1, 1)), name="mrcnn_class_conv2")(x)
    x = KL.TimeDistributed(BatchNorm(axis=3), name='mrcnn_class_bn2')(x)
    x = KL.Activation('relu')(x)

    shared = KL.Lambda(lambda x: KB.squeeze(KB.squeeze(x, 3), 2), name="pool_squeeze")(x)

    # Classifier head
    mrcnn_class_logits = KL.TimeDistributed(KL.Dense(num_classes),name='mrcnn_class_logits')(shared)
    mrcnn_probs        = KL.TimeDistributed(KL.Activation("softmax"), name="mrcnn_class")(mrcnn_class_logits)

    # BBox head
    # [batch, boxes, num_classes * (dy, dx, log(dh), log(dw))]
    x = KL.TimeDistributed(KL.Dense(num_classes * 4, activation='linear'),name='mrcnn_bbox_fc')(shared)
    
    # Reshape to [batch, boxes, num_classes, (dy, dx, log(dh), log(dw))]
    s = KB.int_shape(x)
    mrcnn_bbox = KL.Reshape((s[1], num_classes, 4), name="mrcnn_bbox")(x)

    return mrcnn_class_logits, mrcnn_probs, mrcnn_bbox

    
###############################################################
#  Feature Pyramid Network Head - Mask
###############################################################
def fpn_mask_graph(rois, feature_maps, image_shape, pool_size, num_classes):
    """
    Builds the computation graph of the mask head of Feature Pyramid Network.

    Inputs:
    -------
    rois:               [batch, num_rois, (y1, x1, y2, x2)] 
                        Proposal boxes in normalized coordinates.
    feature_maps:       List of feature maps from diffent layers of the pyramid,
                        [P2, P3, P4, P5]. Each has a different resolution.
    image_shape:        [height, width, depth]
    pool_size:          The width of the square feature map generated from ROI Pooling.
    num_classes:        number of classes, which determines the depth of the results

    Returns:
    --------
: 
                    Masks [batch, roi_count, height, width, num_classes]
    """
    # ROI Pooling
    # Shape: [batch, boxes, pool_height, pool_width, channels]
    print('\n>>> FPN Mask Graph ')
    print('     rois shape          :', rois.get_shape())
    print('     feature_maps :', len(feature_maps))
    for item in feature_maps:
        print('     feature_maps shape  :', item.get_shape())
    print('     input_shape         :', image_shape)
    print('     pool_size           :', pool_size)
    

    x = PyramidROIAlign([pool_size, pool_size], image_shape, name="roi_align_mask")([rois] + feature_maps)

    # Conv layers
    x = KL.TimeDistributed(KL.Conv2D(256, (3, 3), padding="same"), name="mrcnn_mask_conv1")(x)
    x = KL.TimeDistributed(BatchNorm(axis=3), name='mrcnn_mask_bn1')(x)
    x = KL.Activation('relu')(x)

    x = KL.TimeDistributed(KL.Conv2D(256, (3, 3), padding="same"), name="mrcnn_mask_conv2")(x)
    x = KL.TimeDistributed(BatchNorm(axis=3), name='mrcnn_mask_bn2')(x)
    x = KL.Activation('relu')(x)

    x = KL.TimeDistributed(KL.Conv2D(256, (3, 3), padding="same"), name="mrcnn_mask_conv3")(x)
    x = KL.TimeDistributed(BatchNorm(axis=3), name='mrcnn_mask_bn3')(x)
    x = KL.Activation('relu')(x)

    x = KL.TimeDistributed(KL.Conv2D(256, (3, 3), padding="same"), name="mrcnn_mask_conv4")(x)
    x = KL.TimeDistributed(BatchNorm(axis=3), name='mrcnn_mask_bn4')(x)
    x = KL.Activation('relu')(x)

    x = KL.TimeDistributed(KL.Conv2DTranspose(256, (2, 2), strides=2, activation="relu"), name="mrcnn_mask_deconv")(x)
    x = KL.TimeDistributed(KL.Conv2D(num_classes, (1, 1), strides=1, activation="sigmoid"), name="mrcnn_mask")(x)
    
    print('     FPN Mask Graph output shape :', x.get_shape())
    
    return x

