package pl.zabawa.optitransit.models.entities;

import jakarta.persistence.Column;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;
import lombok.*;

@Entity
@Table(name = "stop_transfers")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@ToString
@EqualsAndHashCode(onlyExplicitlyIncluded = true)
public class StopTransferEntity {

    @EmbeddedId
    @EqualsAndHashCode.Include
    private StopTransferId id;

    @Column(name = "min_transfer_time", nullable = false)
    private Integer minTransferTime;

    @Column(name = "distance_meters")
    private Double distanceMeters;
}
